"""Provider abstraction: init_chat_model + structured output into Pydantic models.

Every call is cached (namespace "llm"), budget-checked before it runs, and its tokens and
estimated cost are recorded in Usage. API keys live only inside the chat-model object.
"""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    message_to_dict,
    messages_from_dict,
)
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel

from leadgraph.cache import Cache
from leadgraph.config import ModelsConfig
from leadgraph.usage import Usage

M = TypeVar("M", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    pass


def message_key(m: BaseMessage) -> dict[str, Any]:
    """Stable, provider-independent fingerprint of a message for cache keys."""
    key: dict[str, Any] = {"type": m.type, "content": m.content}
    if isinstance(m, AIMessage) and m.tool_calls:
        key["tool_calls"] = [(c["name"], c["args"], c.get("id")) for c in m.tool_calls]
    if getattr(m, "tool_call_id", None):
        key["tool_call_id"] = m.tool_call_id
    return key


def get_model(provider: str, model: str, api_key: str, *, temperature: float | None = 0,
              max_retries: int = 3, **kwargs: Any) -> BaseChatModel:
    """Return a chat model for any provider supported by init_chat_model."""
    params: dict[str, Any] = {"api_key": api_key, "max_retries": max_retries, **kwargs}
    if temperature is not None:
        params["temperature"] = temperature
    return init_chat_model(model, model_provider=provider, **params)


class LLM:
    """Cached, budgeted structured-output calls for one provider/model."""

    def __init__(self, chat: BaseChatModel, *, provider: str, model: str, cache: Cache,
                 usage: Usage, models_cfg: ModelsConfig, concurrency: int = 6,
                 method: str | None = None):
        self.chat = chat
        self.provider = provider
        self.model = model
        self.cache = cache
        self.usage = usage
        self.price = models_cfg.price(provider, model)
        prov = models_cfg.providers.get(provider)
        self.method = method or (prov.structured_method if prov else "function_calling")
        self._sem = asyncio.Semaphore(concurrency)

    def _record(self, raw: Any, meter: dict[str, float] | None = None) -> tuple[int, int]:
        meta = getattr(raw, "usage_metadata", None) or {}
        in_tok = int(meta.get("input_tokens", 0) or 0)
        out_tok = int(meta.get("output_tokens", 0) or 0)
        cost = self.usage.add_llm(self.model, in_tok, out_tok,
                                  self.price.input_per_mtok, self.price.output_per_mtok)
        if meter is not None:  # per-company accounting (research agent)
            meter["llm_calls"] = meter.get("llm_calls", 0) + 1
            meter["input_tokens"] = meter.get("input_tokens", 0) + in_tok
            meter["output_tokens"] = meter.get("output_tokens", 0) + out_tok
            meter["cost_usd"] = meter.get("cost_usd", 0.0) + cost
        return in_tok, out_tok

    @staticmethod
    def _hit(meter: dict[str, float] | None) -> None:
        if meter is not None:
            meter["cache_hits"] = meter.get("cache_hits", 0) + 1

    async def structured(self, schema: type[M], system: str, user: str,
                         meter: dict[str, float] | None = None) -> M:
        key = (self.provider, self.model, schema.__name__, schema.model_json_schema(), system, user)
        return await self._structured(schema, [SystemMessage(system), HumanMessage(user)], key,
                                      meter)

    async def structured_messages(self, schema: type[M], messages: list[BaseMessage],
                                  meter: dict[str, float] | None = None) -> M:
        key = (self.provider, self.model, schema.__name__, schema.model_json_schema(),
               [message_key(m) for m in messages])
        return await self._structured(schema, messages, key, meter)

    async def chat_turn(self, messages: list[BaseMessage], tools: list[Any],
                        meter: dict[str, float] | None = None) -> AIMessage:
        """One tool-calling turn (agent loops). Cached by the full conversation."""
        tool_specs = [convert_to_openai_tool(t) for t in tools]
        key = (self.provider, self.model, "chat_turn", tool_specs,
               [message_key(m) for m in messages])
        hit = self.cache.get("llm", *key)
        if hit is not None:
            self._hit(meter)
            return messages_from_dict([hit["message"]])[0]
        self.usage.check_llm()
        async with self._sem:
            self.usage.check_llm()
            try:
                msg = await self.chat.bind_tools(tools).ainvoke(messages)
            except Exception:
                self.usage.llm_errors += 1
                raise
        self._record(msg, meter)
        self.cache.set("llm", *key, value={"message": message_to_dict(msg)})
        return msg

    async def _structured(self, schema: type[M], messages: list[BaseMessage], key: tuple,
                          meter: dict[str, float] | None = None) -> M:
        hit = self.cache.get("llm", *key)
        if hit is not None:
            self._hit(meter)
            return schema.model_validate(hit["parsed"])

        self.usage.check_llm()
        runnable = self.chat.with_structured_output(schema, method=self.method, include_raw=True)
        async with self._sem:
            self.usage.check_llm()
            try:
                out = await runnable.ainvoke(messages)
            except Exception:
                self.usage.llm_errors += 1
                raise

        raw = out.get("raw") if isinstance(out, dict) else None
        in_tok, out_tok = self._record(raw, meter)

        parsed = out.get("parsed") if isinstance(out, dict) else out
        if parsed is None or (isinstance(out, dict) and out.get("parsing_error")):
            self.usage.llm_errors += 1
            raise StructuredOutputError(f"{schema.__name__}: {out.get('parsing_error')!r}")
        if not isinstance(parsed, schema):
            parsed = schema.model_validate(parsed)
        self.cache.set("llm", *key, value={
            "parsed": parsed.model_dump(mode="json"),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
        })
        return parsed

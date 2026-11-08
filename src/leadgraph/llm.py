"""Provider abstraction: init_chat_model + structured output into Pydantic models.

Every call is cached (namespace "llm"), budget-checked before it runs, and its tokens and
estimated cost are recorded in Usage. API keys live only inside the chat-model object.
"""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from leadgraph.cache import Cache
from leadgraph.config import ModelsConfig
from leadgraph.usage import Usage

M = TypeVar("M", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    pass


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

    async def structured(self, schema: type[M], system: str, user: str) -> M:
        key = (self.provider, self.model, schema.__name__, schema.model_json_schema(), system, user)
        hit = self.cache.get("llm", *key)
        if hit is not None:
            return schema.model_validate(hit["parsed"])

        self.usage.check_llm()
        runnable = self.chat.with_structured_output(schema, method=self.method, include_raw=True)
        async with self._sem:
            self.usage.check_llm()
            try:
                out = await runnable.ainvoke([SystemMessage(system), HumanMessage(user)])
            except Exception:
                self.usage.llm_errors += 1
                raise

        raw = out.get("raw") if isinstance(out, dict) else None
        meta = getattr(raw, "usage_metadata", None) or {}
        in_tok = int(meta.get("input_tokens", 0) or 0)
        out_tok = int(meta.get("output_tokens", 0) or 0)
        self.usage.add_llm(self.model, in_tok, out_tok,
                           self.price.input_per_mtok, self.price.output_per_mtok)

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

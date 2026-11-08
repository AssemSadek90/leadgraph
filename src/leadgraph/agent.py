"""Bounded tool loop shared by the agents (scout in M2, research in M3).

The model may call tools until `max_tool_calls` is used up or it stops calling tools; then a
final structured-output call turns the conversation into a Pydantic object. Every turn and
every tool result is cached, so a resumed run replays the loop without paid calls.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, ValidationError, create_model

from leadgraph.llm import LLM

M = TypeVar("M", bound=BaseModel)


@dataclass
class Tool:
    name: str
    description: str
    args: dict[str, tuple[type, Any]]  # pydantic field definitions
    fn: Callable[..., Awaitable[str]]
    schema: type[BaseModel] = field(init=False)

    def __post_init__(self) -> None:
        self.schema = create_model(self.name, __doc__=self.description, **self.args)


@dataclass
class AgentTrace:
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"tool_calls": self.tool_calls, "turns": self.turns, "stop_reason": self.stop_reason}


async def run_tool_loop(
    llm: LLM, *, system: str, user: str, tools: list[Tool], final_schema: type[M],
    max_tool_calls: int, final_instruction: str, result_chars: int = 4000,
) -> tuple[M, AgentTrace]:
    by_name = {t.name: t for t in tools}
    schemas = [t.schema for t in tools]
    messages: list[BaseMessage] = [SystemMessage(system), HumanMessage(user)]
    trace = AgentTrace()
    used = 0

    for _ in range(max_tool_calls + 2):  # hard cap on turns
        ai = await llm.chat_turn(messages, schemas)
        trace.turns += 1
        messages.append(ai)
        if not ai.tool_calls:
            trace.stop_reason = "model_finished"
            break
        for call in ai.tool_calls:
            name, args, call_id = call["name"], call.get("args") or {}, call.get("id") or ""
            if used >= max_tool_calls:
                content = "Tool budget exhausted. Do not call more tools; give your final answer."
                trace.tool_calls.append({"tool": name, "args": args, "status": "refused_budget"})
            elif name not in by_name:
                content = f"ERROR: unknown tool '{name}'."
                trace.tool_calls.append({"tool": name, "args": args, "status": "unknown_tool"})
            else:
                used += 1
                tool = by_name[name]
                try:
                    parsed = tool.schema.model_validate(args)
                    content = await tool.fn(**parsed.model_dump())
                    status = "ok"
                except ValidationError as exc:
                    content, status = f"ERROR: invalid arguments: {exc.errors()[:2]}", "bad_args"
                except Exception as exc:  # tool errors go back to the model, not up the stack
                    content, status = f"ERROR: {type(exc).__name__}: {str(exc)[:300]}", "error"
                trace.tool_calls.append({"tool": name, "args": args, "status": status})
            messages.append(ToolMessage(content=str(content)[:result_chars], tool_call_id=call_id))
        if used >= max_tool_calls:
            trace.stop_reason = "tool_budget"
            break
    else:
        trace.stop_reason = "turn_cap"

    messages.append(HumanMessage(final_instruction))
    final = await llm.structured_messages(final_schema, messages)
    return final, trace


def compact_json(obj: Any, limit: int = 4000) -> str:
    return json.dumps(obj, ensure_ascii=False)[:limit]

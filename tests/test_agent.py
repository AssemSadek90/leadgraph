from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel

from leadgraph.agent import Tool, run_tool_loop
from leadgraph.llm import LLM
from tests.fakes import ScriptedChat, tool_call


class Final(BaseModel):
    answer: str


def make(chat, cache, usage, models):
    return LLM(chat, provider="anthropic", model="claude-haiku-5-5", cache=cache, usage=usage,
               models_cfg=models)


async def echo(text: str) -> str:
    return f"echo:{text}"


TOOLS = [Tool("echo", "Echo text.", {"text": (str, ...)}, echo)]


async def test_loop_runs_tools_then_final(cache, usage, models):
    def script(messages):
        n = sum(isinstance(m, ToolMessage) for m in messages)
        return tool_call("echo", {"text": f"hi{n}"}, f"c{n}") if n < 2 else AIMessage(content="done")

    seen = {}

    def responder(schema, messages):
        seen["tool_results"] = [m.content for m in messages if isinstance(m, ToolMessage)]
        return {"answer": "ok"}

    chat = ScriptedChat(responder, script)
    final, trace = await run_tool_loop(make(chat, cache, usage, models), system="s", user="u",
                                       tools=TOOLS, final_schema=Final, max_tool_calls=5,
                                       final_instruction="final")
    assert final.answer == "ok" and trace.stop_reason == "model_finished"
    assert [c["status"] for c in trace.tool_calls] == ["ok", "ok"]
    assert seen["tool_results"] == ["echo:hi0", "echo:hi1"]

    # replay: every turn comes from the cache, no new model calls
    calls = chat.calls
    final2, _ = await run_tool_loop(make(chat, cache, usage, models), system="s", user="u",
                                    tools=TOOLS, final_schema=Final, max_tool_calls=5,
                                    final_instruction="final")
    assert final2 == final and chat.calls == calls


async def test_tool_budget_and_unknown_tool(cache, usage, models):
    def script(messages):
        n = sum(isinstance(m, ToolMessage) for m in messages)
        return AIMessage(content="", tool_calls=[
            {"name": "echo", "args": {"text": "a"}, "id": f"a{n}", "type": "tool_call"},
            {"name": "nope", "args": {}, "id": f"b{n}", "type": "tool_call"},
            {"name": "echo", "args": {"text": "c"}, "id": f"c{n}", "type": "tool_call"},
        ])

    chat = ScriptedChat(lambda s, m: {"answer": "x"}, script)
    _, trace = await run_tool_loop(make(chat, cache, usage, models), system="s", user="u",
                                   tools=TOOLS, final_schema=Final, max_tool_calls=3,
                                   final_instruction="final")
    statuses = [c["status"] for c in trace.tool_calls]
    assert statuses.count("ok") == 3 and "unknown_tool" in statuses
    assert "refused_budget" in statuses and trace.stop_reason == "tool_budget"


async def test_tool_errors_go_back_to_model(cache, usage, models):
    async def boom(text: str) -> str:
        raise ValueError("bad")

    tools = [Tool("echo", "Echo.", {"text": (str, ...)}, boom)]

    def script(messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            assert "ERROR: ValueError" in messages[-1].content
            return AIMessage(content="ok")
        return tool_call("echo", {"text": "x"}, "c0")

    chat = ScriptedChat(lambda s, m: {"answer": "x"}, script)
    _, trace = await run_tool_loop(make(chat, cache, usage, models), system="s", user="u",
                                   tools=tools, final_schema=Final, max_tool_calls=3,
                                   final_instruction="final")
    assert trace.tool_calls[0]["status"] == "error"

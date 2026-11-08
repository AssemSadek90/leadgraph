import pytest

from leadgraph.graph.nodes.classify import classification_schema
from leadgraph.llm import LLM, get_model
from leadgraph.usage import BudgetExceeded, Usage
from tests.fakes import FakeChat
from tests.mock_llm_server import MockLLMServer

ANSWER = {"category": "hausverwaltung", "confidence": "high", "reason": "WEG-Verwaltung offered."}


def make_llm(chat, cache, usage, models, provider="anthropic", model="claude-haiku-5-5"):
    return LLM(chat, provider=provider, model=model, cache=cache, usage=usage, models_cfg=models)


async def test_structured_cache_and_cost(cache, usage, models, segment):
    chat = FakeChat(lambda s, m: ANSWER)
    llm = make_llm(chat, cache, usage, models)
    schema = classification_schema(segment)
    a = await llm.structured(schema, "sys", "user text")
    b = await llm.structured(schema, "sys", "user text")
    assert a == b and a.category == "hausverwaltung"
    assert chat.calls == 1 and cache.hits["llm"] == 1
    # haiku: 1000 in @ $0.10/M + 50 out @ $0.50/M
    assert usage.llm_cost_usd == pytest.approx(0.000125)
    assert usage.input_tokens == 1000 and usage.output_tokens == 50
    await llm.structured(schema, "sys", "other text")
    assert chat.calls == 2


async def test_budget_guard_before_call(cache, models, segment):
    usage = Usage(max_places_requests=1, max_llm_cost_usd=0.0001)
    chat = FakeChat(lambda s, m: ANSWER)
    llm = make_llm(chat, cache, usage, models)
    schema = classification_schema(segment)
    await llm.structured(schema, "sys", "one")  # 0.000125 spent → over budget
    with pytest.raises(BudgetExceeded):
        await llm.structured(schema, "sys", "two")
    assert chat.calls == 1
    # cached answers stay free even over budget
    assert (await llm.structured(schema, "sys", "one")).category == "hausverwaltung"


@pytest.mark.parametrize("provider,model,path", [
    ("anthropic", "claude-haiku-5-5", "/v1/messages"),
    ("openai", "gpt-6-luna", "/v1/chat/completions"),
])
async def test_real_provider_path_against_mock_server(provider, model, path, cache, usage,
                                                      models, segment, monkeypatch, tmp_path):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    key = "sk-test-SECRET-123"
    with MockLLMServer(ANSWER) as srv:
        base = srv.url + ("/v1" if provider == "openai" else "")
        temperature = 0 if models.providers[provider].send_temperature else None
        chat = get_model(provider, model, key, temperature=temperature, max_retries=0, base_url=base)
        llm = LLM(chat, provider=provider, model=model, cache=cache, usage=usage, models_cfg=models)
        out = await llm.structured(classification_schema(segment), "system", "user")
    assert out.category == "hausverwaltung"
    assert srv.requests[0][0] == path
    assert usage.llm_calls == 1 and usage.input_tokens > 0
    body = srv.requests[0][1]
    assert ("temperature" in body) == models.providers[provider].send_temperature
    # the key is never written into the cache
    cache.close()
    for f in (tmp_path / ".cache").rglob("*"):
        if f.is_file():
            assert key.encode() not in f.read_bytes()

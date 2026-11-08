"""Live smoke tests (paid APIs). Run with: uv run pytest -m live -s

These read keys from the real environment, so they skip the autouse env scrub.
"""

import os

import httpx
import pytest

from leadgraph.cache import Cache
from leadgraph.config import load_city, load_models, load_segment, load_settings
from leadgraph.graph.nodes.classify import classification_schema
from leadgraph.llm import LLM, get_model
from leadgraph.sources.places import PlacesClient
from leadgraph.usage import Usage

pytestmark = pytest.mark.live
LIVE_ENV = {k: os.environ.get(k) for k in
            ("GOOGLE_PLACES_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")}


@pytest.fixture
def live_env(monkeypatch):
    for k, v in LIVE_ENV.items():
        if v:
            monkeypatch.setenv(k, v)


@pytest.mark.skipif(not LIVE_ENV["GOOGLE_PLACES_API_KEY"], reason="no GOOGLE_PLACES_API_KEY")
async def test_places_one_page(live_env, tmp_path):
    s = load_settings()
    s.places.max_pages = 1
    usage = Usage(max_places_requests=1, max_llm_cost_usd=0)
    async with httpx.AsyncClient() as http:
        client = PlacesClient(os.environ["GOOGLE_PLACES_API_KEY"], http, Cache(tmp_path),
                              usage, s.places)
        res = await client.search("Hausverwaltung", load_city("berlin").area("mitte"))
    assert res.places, "no results"
    print(f"\n{len(res.places)} places, first: {res.places[0]['displayName']['text']}")


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
async def test_llm_structured(provider, live_env, tmp_path):
    models = load_models()
    cfg = models.providers[provider]
    if not os.environ.get(cfg.env_key):
        pytest.skip(f"no {cfg.env_key}")
    usage = Usage(max_places_requests=0, max_llm_cost_usd=0.05)
    temperature = 0 if cfg.send_temperature else None
    chat = get_model(provider, cfg.default_model, os.environ[cfg.env_key], temperature=temperature)
    llm = LLM(chat, provider=provider, model=cfg.default_model, cache=Cache(tmp_path),
              usage=usage, models_cfg=models)
    out = await llm.structured(
        classification_schema(load_segment("hausverwaltung")),
        "Classify the business. Pick one category.",
        "Business name: Muster Hausverwaltung GmbH\nHomepage text: WEG-Verwaltung und "
        "Mietverwaltung in Berlin seit 1998.",
    )
    print(f"\n{provider}/{cfg.default_model}: {out} tokens={usage.input_tokens}/{usage.output_tokens}")
    assert out.category == "hausverwaltung"

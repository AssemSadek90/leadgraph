from __future__ import annotations

import json
from pathlib import Path

import pytest

from leadgraph.cache import Cache
from leadgraph.config import load_city, load_models, load_segment, load_settings
from leadgraph.usage import Usage

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_provider_env(monkeypatch):
    # The SDKs read these; tests must never use real endpoints or keys.
    for var in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                "OPENAI_BASE_URL", "GOOGLE_PLACES_API_KEY", "SERPER_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def city():
    return load_city("berlin")


@pytest.fixture
def segment():
    return load_segment("hausverwaltung")


@pytest.fixture
def settings():
    s = load_settings()
    s.fetch.per_domain_interval_s = 0.0
    return s


@pytest.fixture
def models():
    return load_models()


@pytest.fixture
def cache(tmp_path):
    c = Cache(tmp_path / ".cache")
    yield c
    c.close()


@pytest.fixture
def usage():
    return Usage(max_places_requests=50, max_llm_cost_usd=1.0,
                 places_sku_usd_per_1000=load_models().places.sku_usd_per_1000)


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

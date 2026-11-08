"""Config loading. YAML files under config/ → typed Pydantic models."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("LEADGRAPH_CONFIG_DIR", REPO_ROOT / "config"))
OUTPUTS_DIR = Path(os.environ.get("LEADGRAPH_OUTPUTS_DIR", REPO_ROOT / "outputs"))


class Rect(BaseModel):
    south: float
    west: float
    north: float
    east: float

    def to_places(self) -> dict[str, Any]:
        return {
            "rectangle": {
                "low": {"latitude": self.south, "longitude": self.west},
                "high": {"latitude": self.north, "longitude": self.east},
            }
        }

    def key(self) -> str:
        return f"{self.south:.5f},{self.west:.5f},{self.north:.5f},{self.east:.5f}"


class CityConfig(BaseModel):
    name: str
    label: str
    bbox: Rect
    test_areas: dict[str, Rect] = {}
    postcode_prefixes: list[str] = []
    postcode_ranges: list[tuple[int, int]] = []
    locality_names: list[str] = []

    def area(self, name: str | None) -> Rect:
        if not name:
            return self.bbox
        if name not in self.test_areas:
            raise KeyError(f"Unknown area '{name}'. Known: {sorted(self.test_areas)}")
        return self.test_areas[name]


class SegmentConfig(BaseModel):
    name: str
    label: str
    queries: list[str]
    categories: dict[str, str]
    include_categories: list[str]
    classification_instructions: str
    weights: dict[str, float] = {}
    buckets: dict[str, dict[str, float]] = {}
    tiers: dict[str, float] = {}


class ModelPrice(BaseModel):
    input_per_mtok: float
    output_per_mtok: float


class ProviderConfig(BaseModel):
    env_key: str
    default_model: str
    structured_method: str = "function_calling"
    send_temperature: bool = True  # some reasoning models reject a non-default temperature
    models: dict[str, ModelPrice] = {}


class PlacesPricing(BaseModel):
    env_key: str = "GOOGLE_PLACES_API_KEY"
    sku_usd_per_1000: dict[str, float] = {}
    free_per_month: dict[str, int] = {}


class SearchPricing(BaseModel):
    env_key: str = "SERPER_API_KEY"
    usd_per_1000: float = 1.0


class ModelsConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    fallback_price: ModelPrice
    places: PlacesPricing = PlacesPricing()
    search: SearchPricing = SearchPricing()

    def price(self, provider: str, model: str) -> ModelPrice:
        prov = self.providers.get(provider)
        if prov and model in prov.models:
            return prov.models[model]
        return self.fallback_price


class FetchSettings(BaseModel):
    user_agent: str
    timeout_s: float = 15
    per_domain_interval_s: float = 1.0
    global_concurrency: int = 8
    max_retries: int = 3
    max_bytes: int = 3_000_000


class LLMSettings(BaseModel):
    concurrency: int = 6
    temperature: float | None = 0
    max_retries: int = 3
    homepage_chars: int = 2000


class BudgetSettings(BaseModel):
    max_places_requests: int = 300
    max_llm_cost_usd: float = 2.0
    max_search_requests: int = 100


class PlacesSettings(BaseModel):
    tier: Literal["pro", "enterprise"] = "pro"
    page_size: int = 20
    max_pages: int = 3
    language_code: str = "de"
    region_code: str = "DE"
    concurrency: int = 4
    lookup_page_size: int = 5


class DiscoverySettings(BaseModel):
    max_rounds: int = 4
    yield_threshold: float = 0.02
    min_tile_km: float = 1.0
    eager_split_km: float = 6.0
    max_list_pages: int = 10
    list_chunk_chars: int = 20000
    max_chunks_per_page: int = 5
    max_expansion_queries: int = 5


class AgentSettings(BaseModel):
    scout_max_tool_calls: int = 15
    tool_result_chars: int = 4000


class Settings(BaseModel):
    fetch: FetchSettings
    llm: LLMSettings = LLMSettings()
    budget: BudgetSettings = BudgetSettings()
    places: PlacesSettings = PlacesSettings()
    discovery: DiscoverySettings = DiscoverySettings()
    agents: AgentSettings = AgentSettings()


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_city(name: str, config_dir: Path = CONFIG_DIR) -> CityConfig:
    return CityConfig.model_validate(_load_yaml(config_dir / "cities" / f"{name}.yaml"))


def load_segment(name: str, config_dir: Path = CONFIG_DIR) -> SegmentConfig:
    return SegmentConfig.model_validate(_load_yaml(config_dir / "segments" / f"{name}.yaml"))


def load_models(config_dir: Path = CONFIG_DIR) -> ModelsConfig:
    return ModelsConfig.model_validate(_load_yaml(config_dir / "models.yaml"))


def load_settings(config_dir: Path = CONFIG_DIR) -> Settings:
    return Settings.model_validate(_load_yaml(config_dir / "settings.yaml"))

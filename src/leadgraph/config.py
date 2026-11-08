"""Config loading. YAML files under config/ → typed Pydantic models."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

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
    usd_per_request: float = 0.035
    free_requests_per_month: int = 0


class ModelsConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    fallback_price: ModelPrice
    places: PlacesPricing = PlacesPricing()

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


class PlacesSettings(BaseModel):
    page_size: int = 20
    max_pages: int = 3
    language_code: str = "de"
    region_code: str = "DE"
    concurrency: int = 4


class Settings(BaseModel):
    fetch: FetchSettings
    llm: LLMSettings = LLMSettings()
    budget: BudgetSettings = BudgetSettings()
    places: PlacesSettings = PlacesSettings()


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

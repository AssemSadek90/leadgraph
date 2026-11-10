"""Data contract (BUILD_PLAN Section 3.1). This file is the source of truth."""

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")


class Sourced(BaseModel, Generic[T]):
    value: T | None = None
    source_url: str | None = None
    evidence: str | None = Field(None, max_length=300)  # verbatim from the page
    confidence: Literal["high", "medium", "low"] | None = None


class Contact(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None
    email_type: Literal["personal", "role", "generic"] | None = None
    linkedin_url: str | None = None
    linkedin_verified: bool = False  # always False until a human checks
    source_url: str


class CompanyKPIs(BaseModel):
    units_managed: Sourced[int]
    management_types: Sourced[list[Literal["WEG", "MIET", "SEV", "GEWERBE"]]]
    residential_focus: Sourced[bool]
    portfolio_scope: Sourced[Literal["city", "regional", "national"]]  # city = the run's city
    tenant_portal_or_app: Sourced[bool]
    software_mentioned: Sourced[list[str]]
    metering_provider_mentioned: Sourced[list[str]]
    remote_reading_or_uvi_mentioned: Sourced[bool]
    association_membership: Sourced[list[str]]  # VDIV, IVD, BVI, ...
    employee_count: Sourced[int]

    @field_validator("portfolio_scope", mode="before")
    @classmethod
    def _legacy_scope(cls, v: Any) -> Any:
        # Runs before any-city support (9 Nov 2026) stored "berlin" for city-only portfolios.
        if isinstance(v, dict) and v.get("value") == "berlin":
            return {**v, "value": "city"}
        if isinstance(v, Sourced) and v.value == "berlin":
            return v.model_copy(update={"value": "city"})
        return v


class Company(BaseModel):
    id: str  # normalized domain, else hash(name + postcode)
    name: str
    address: str | None = None
    postcode: str | None = None
    website: str | None = None
    phone: str | None = None
    google_place_id: str | None = None
    google_rating: float | None = None
    google_review_count: int | None = None
    google_types: list[str] = []  # extension: Places primaryType first, then types
    discovery_sources: list[str] = []
    first_seen_round: int | None = None
    is_property_manager: bool | None = None
    classification_category: str | None = None  # extension: category chosen by classify
    classification_reason: str | None = None
    kpis: CompanyKPIs | None = None
    contacts: list[Contact] = []
    fit_score: float | None = None
    tier: Literal["A", "B", "C"] | None = None
    score_breakdown: dict[str, float] = {}
    data_completeness: float | None = None
    needs_review: bool = False
    review_reasons: list[str] = []


class Candidate(BaseModel):
    """A raw record from one discovery source, before filtering and dedupe."""

    name: str
    source: str  # e.g. "places:Hausverwaltung" or a directory URL
    round: int = 1
    address: str | None = None
    postcode: str | None = None
    locality: str | None = None
    website: str | None = None
    phone: str | None = None
    google_place_id: str | None = None
    google_rating: float | None = None
    google_review_count: int | None = None
    business_status: str | None = None
    types: list[str] = []
    primary_type: str | None = None
    contact_fields: bool = False  # website/phone were requested from Places (Enterprise tier)


class ExcludedRecord(BaseModel):
    name: str
    reason: str
    address: str | None = None
    postcode: str | None = None
    website: str | None = None
    google_place_id: str | None = None
    source: str | None = None

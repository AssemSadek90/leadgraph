"""Run-wide usage counters and the budget guard (checked before every paid call)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any


class BudgetExceeded(RuntimeError):
    """Raised before a paid call when the run's budget is used up."""


@dataclass
class Usage:
    max_places_requests: int
    max_llm_cost_usd: float
    max_search_requests: int = 100
    places_sku_usd_per_1000: dict[str, float] = field(default_factory=dict)

    places_requests: int = 0
    places_by_sku: Counter[str] = field(default_factory=Counter)
    search_requests: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_cost_usd: float = 0.0
    llm_errors: int = 0
    per_model: dict[str, dict[str, float]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(float))
    )
    http_requests: int = 0
    robots_blocked: int = 0
    fetch_errors: int = 0
    budget_events: list[str] = field(default_factory=list)

    # --- budget guard -------------------------------------------------------------
    def _exceeded(self, msg: str) -> BudgetExceeded:
        if msg not in self.budget_events:
            self.budget_events.append(msg)
        return BudgetExceeded(msg)

    def reserve_places_request(self, sku: str = "text_search_enterprise") -> None:
        """Check and count one Places request. Call right before the HTTP request."""
        if self.places_requests >= self.max_places_requests:
            raise self._exceeded(f"places budget reached ({self.max_places_requests} requests)")
        self.places_requests += 1
        self.places_by_sku[sku] += 1

    def reserve_search_request(self) -> None:
        if self.search_requests >= self.max_search_requests:
            raise self._exceeded(f"search budget reached ({self.max_search_requests} requests)")
        self.search_requests += 1

    @property
    def places_cost_usd(self) -> float:
        return sum(n * self.places_sku_usd_per_1000.get(sku, 35.0) / 1000
                   for sku, n in self.places_by_sku.items())

    def check_llm(self) -> None:
        if self.llm_cost_usd >= self.max_llm_cost_usd:
            raise self._exceeded(f"LLM budget reached (${self.max_llm_cost_usd:.2f})")

    # --- accounting ---------------------------------------------------------------
    def add_llm(self, model: str, input_tokens: int, output_tokens: int,
                input_per_mtok: float, output_per_mtok: float) -> float:
        cost = input_tokens / 1e6 * input_per_mtok + output_tokens / 1e6 * output_per_mtok
        self.llm_calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.llm_cost_usd += cost
        m = self.per_model[model]
        m["calls"] += 1
        m["input_tokens"] += input_tokens
        m["output_tokens"] += output_tokens
        m["cost_usd"] += cost
        return cost

    def to_dict(self) -> dict[str, Any]:
        return {
            "places_requests": self.places_requests,
            "places_by_sku": dict(self.places_by_sku),
            "places_cost_estimate_usd": round(self.places_cost_usd, 4),
            "search_requests": self.search_requests,
            "llm_calls": self.llm_calls,
            "llm_errors": self.llm_errors,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "llm_cost_estimate_usd": round(self.llm_cost_usd, 6),
            "per_model": {k: dict(v) for k, v in self.per_model.items()},
            "http_requests": self.http_requests,
            "robots_blocked": self.robots_blocked,
            "fetch_errors": self.fetch_errors,
            "budget": {
                "max_places_requests": self.max_places_requests,
                "max_llm_cost_usd": self.max_llm_cost_usd,
                "max_search_requests": self.max_search_requests,
                "events": self.budget_events,
            },
        }

    # --- persistence (budget continuity across resumes) -------------------------------
    COUNTERS = ("places_requests", "search_requests", "llm_calls", "input_tokens",
                "output_tokens", "llm_cost_usd", "llm_errors", "http_requests",
                "robots_blocked", "fetch_errors")

    def to_counters(self) -> dict[str, Any]:
        d: dict[str, Any] = {k: getattr(self, k) for k in self.COUNTERS}
        d["places_by_sku"] = dict(self.places_by_sku)
        d["per_model"] = {k: dict(v) for k, v in self.per_model.items()}
        d["budget_events"] = list(self.budget_events)
        return d

    def load_counters(self, d: dict[str, Any]) -> None:
        for k in self.COUNTERS:
            if k in d:
                setattr(self, k, d[k])
        self.places_by_sku.update(d.get("places_by_sku") or {})
        for model, vals in (d.get("per_model") or {}).items():
            for k, v in vals.items():
                self.per_model[model][k] += v
        # budget events are not restored: a resume may raise the budget on purpose

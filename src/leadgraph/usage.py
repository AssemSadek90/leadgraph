"""Run-wide usage counters and the budget guard (checked before every paid call)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


class BudgetExceeded(RuntimeError):
    """Raised before a paid call when the run's budget is used up."""


@dataclass
class Usage:
    max_places_requests: int
    max_llm_cost_usd: float
    places_usd_per_request: float = 0.0

    places_requests: int = 0
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
    def reserve_places_request(self) -> None:
        """Check and count one Places request. Call right before the HTTP request."""
        if self.places_requests >= self.max_places_requests:
            msg = f"places budget reached ({self.max_places_requests} requests)"
            if msg not in self.budget_events:
                self.budget_events.append(msg)
            raise BudgetExceeded(msg)
        self.places_requests += 1

    def check_llm(self) -> None:
        if self.llm_cost_usd >= self.max_llm_cost_usd:
            msg = f"LLM budget reached (${self.max_llm_cost_usd:.2f})"
            if msg not in self.budget_events:
                self.budget_events.append(msg)
            raise BudgetExceeded(msg)

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
            "places_cost_estimate_usd": round(
                self.places_requests * self.places_usd_per_request, 4),
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
                "events": self.budget_events,
            },
        }

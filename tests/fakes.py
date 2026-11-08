"""Fakes for offline tests: Places pages, a tiny fake web, and a fake chat model."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from langchain_core.messages import AIMessage


def make_place(i: int, **over: Any) -> dict[str, Any]:
    pc = over.pop("postcode", "10115")
    locality = over.pop("locality", "Berlin")
    place = {
        "id": f"place-{i}",
        "displayName": {"text": f"Hausverwaltung Nummer {i} GmbH", "languageCode": "de"},
        "formattedAddress": f"Teststraße {i}, {pc} {locality}, Deutschland",
        "addressComponents": [
            {"longText": str(i), "shortText": str(i), "types": ["street_number"]},
            {"longText": pc, "shortText": pc, "types": ["postal_code"]},
            {"longText": locality, "shortText": locality, "types": ["locality", "political"]},
        ],
        "websiteUri": f"https://hv{i}.example.de/",
        "nationalPhoneNumber": f"030 {1000000 + i}",
        "rating": 4.2,
        "userRatingCount": 10 + i,
        "businessStatus": "OPERATIONAL",
        "types": ["real_estate_agency", "point_of_interest", "establishment"],
        "primaryType": "real_estate_agency",
    }
    place.update(over)
    return place


def apply_mask(place: dict[str, Any], mask: str, prefix: str = "places.") -> dict[str, Any]:
    """Return only the fields the field mask asks for (like the real API)."""
    fields = {f.removeprefix(prefix) for f in mask.split(",")}
    return {k: v for k, v in place.items() if k in fields}


class PlacesFake:
    """respx side effect: serves `pages` (list of place lists) via nextPageToken.

    Responses honour the X-Goog-FieldMask header, so Pro-tier requests get no website/phone.
    """

    def __init__(self, pages: list[list[dict[str, Any]]]):
        self.pages = pages
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)
        idx = int(body.get("pageToken", "page-0").split("-")[1])
        mask = request.headers["X-Goog-FieldMask"]
        data: dict[str, Any] = {"places": [apply_mask(p, mask) for p in self.pages[idx]]}
        if idx + 1 < len(self.pages):
            data["nextPageToken"] = f"page-{idx + 1}"
        return httpx.Response(200, json=data)


class DetailsFake:
    """respx side effect for GET /v1/places/{id}: Enterprise fields of known places."""

    def __init__(self, places: list[dict[str, Any]]):
        self.by_id = {p["id"]: p for p in places}
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        pid = request.url.path.rsplit("/", 1)[-1]
        self.calls.append(pid)
        if pid not in self.by_id:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        return httpx.Response(200, json=apply_mask(self.by_id[pid],
                                                   request.headers["X-Goog-FieldMask"], ""))


DETAILS_ROUTE = r"https://places\.googleapis\.com/v1/places/[^/:]+$"


def html_page(title: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


def mount_site(router, base: str, pages: dict[str, str], robots: str = "User-agent: *\nAllow: /\n"):
    router.get(f"{base}/robots.txt").mock(return_value=httpx.Response(200, text=robots))
    for path, html in pages.items():
        router.get(f"{base}{path}").mock(return_value=httpx.Response(
            200, text=html, headers={"content-type": "text/html; charset=utf-8"}))


class FakeChat:
    """Duck-typed chat model: with_structured_output(...).ainvoke(messages)."""

    def __init__(self, responder: Callable[[type, list], dict[str, Any]],
                 input_tokens: int = 1000, output_tokens: int = 50):
        self.responder = responder
        self.calls = 0
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def with_structured_output(self, schema, method=None, include_raw=False, **_):
        outer = self

        class _Runnable:
            async def ainvoke(self, messages):
                outer.calls += 1
                parsed = schema.model_validate(outer.responder(schema, messages))
                raw = AIMessage(content="", usage_metadata={
                    "input_tokens": outer.input_tokens, "output_tokens": outer.output_tokens,
                    "total_tokens": outer.input_tokens + outer.output_tokens})
                return {"raw": raw, "parsed": parsed, "parsing_error": None}

        return _Runnable()


def classify_responder(schema, messages) -> dict[str, Any]:
    text = messages[-1].content.lower()
    if "makler" in text and "verwaltung" not in text.split("homepage text")[-1]:
        return {"category": "makler", "confidence": "high", "reason": "Homepage offers only sales."}
    if "verwaltung" in text:
        return {"category": "hausverwaltung", "confidence": "high",
                "reason": "Homepage mentions WEG-Verwaltung."}
    return {"category": "other", "confidence": "low", "reason": "No evidence."}


class ScriptedChat(FakeChat):
    """FakeChat that also supports bind_tools: `tool_script(messages) -> AIMessage`."""

    def __init__(self, responder, tool_script, **kw):
        super().__init__(responder, **kw)
        self.tool_script = tool_script
        self.tool_turns = 0

    def bind_tools(self, tools, **_):
        outer = self

        class _Runnable:
            async def ainvoke(self, messages):
                outer.calls += 1
                outer.tool_turns += 1
                msg = outer.tool_script(messages)
                msg.usage_metadata = {"input_tokens": outer.input_tokens,
                                      "output_tokens": outer.output_tokens,
                                      "total_tokens": outer.input_tokens + outer.output_tokens}
                return msg

        return _Runnable()


def tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id,
                                              "type": "tool_call"}])

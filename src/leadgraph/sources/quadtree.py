"""Quadtree sweep over a bounding box with Places Text Search.

A tile whose result set is full (every page full, i.e. the 60-result cap was hit) may hold
more businesses than Places returns, so it is split into 4 children and each child is
searched again, down to `min_tile_km`. Tiles larger than `eager_split_km` are split as soon
as their first page is full, which skips two paid pages that the children re-cover anyway.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from leadgraph.config import Rect
from leadgraph.usage import BudgetExceeded

KM_PER_DEG_LAT = 111.32


class PageSearcher(Protocol):
    async def search_page(self, query: str, rect: Rect,
                          page_token: str | None = None) -> dict[str, Any]: ...


def tile_size_km(rect: Rect) -> tuple[float, float]:
    """(width_km, height_km) of a lat/lng rectangle."""
    mid_lat = math.radians((rect.north + rect.south) / 2)
    height = (rect.north - rect.south) * KM_PER_DEG_LAT
    width = (rect.east - rect.west) * KM_PER_DEG_LAT * math.cos(mid_lat)
    return width, height


def split(rect: Rect) -> list[Rect]:
    mid_lat = (rect.north + rect.south) / 2
    mid_lng = (rect.east + rect.west) / 2
    return [
        Rect(south=rect.south, west=rect.west, north=mid_lat, east=mid_lng),
        Rect(south=rect.south, west=mid_lng, north=mid_lat, east=rect.east),
        Rect(south=mid_lat, west=rect.west, north=rect.north, east=mid_lng),
        Rect(south=mid_lat, west=mid_lng, north=rect.north, east=rect.east),
    ]


def can_split(rect: Rect, min_tile_km: float) -> bool:
    """Children (half the size) must not be smaller than min_tile_km on their longer side."""
    return max(tile_size_km(rect)) / 2 >= min_tile_km


@dataclass
class TileResult:
    query: str
    rect: Rect
    depth: int
    places: list[dict[str, Any]] = field(default_factory=list)
    pages: int = 0
    capped: bool = False
    split: bool = False
    eager: bool = False
    error: str | None = None

    def log(self) -> dict[str, Any]:
        w, h = tile_size_km(self.rect)
        return {
            "query": self.query, "rect": self.rect.key(), "depth": self.depth,
            "size_km": [round(w, 2), round(h, 2)], "results": len(self.places),
            "pages": self.pages, "capped": self.capped, "split": self.split,
            "eager_split": self.eager, "error": self.error,
        }


async def search_tile(client: PageSearcher, query: str, rect: Rect, depth: int, *,
                      page_size: int, max_pages: int, min_tile_km: float,
                      eager_split_km: float) -> TileResult:
    res = TileResult(query=query, rect=rect, depth=depth)
    splittable = can_split(rect, min_tile_km)
    eager_ok = eager_split_km > 0 and max(tile_size_km(rect)) > eager_split_km and splittable
    token: str | None = None
    full_pages = 0
    for _ in range(max_pages):
        data = await client.search_page(query, rect, token)
        places = data.get("places", []) or []
        res.places.extend(places)
        res.pages += 1
        token = data.get("nextPageToken")
        if len(places) >= page_size:
            full_pages += 1
        if not token:
            break
        if eager_ok and full_pages == 1:
            res.capped, res.eager = True, True
            break
    if not res.eager:
        res.capped = full_pages == max_pages or bool(token)
    res.split = res.capped and splittable
    return res


async def quadtree_sweep(
    client: PageSearcher, query: str, root: Rect, *, page_size: int = 20, max_pages: int = 3,
    min_tile_km: float = 1.0, eager_split_km: float = 0.0, max_depth: int = 12,
    on_tile: Callable[[TileResult], Awaitable[None] | None] | None = None,
) -> tuple[list[TileResult], str | None]:
    """Breadth-first sweep. Returns (tiles, stop_reason); stop_reason is set on budget stop."""
    tiles: list[TileResult] = []
    level: list[tuple[Rect, int]] = [(root, 0)]
    stop: str | None = None

    async def one(rect: Rect, depth: int) -> TileResult | None:
        nonlocal stop
        if stop:
            return None
        try:
            return await search_tile(client, query, rect, depth, page_size=page_size,
                                     max_pages=max_pages, min_tile_km=min_tile_km,
                                     eager_split_km=eager_split_km)
        except BudgetExceeded as exc:
            stop = f"budget: {exc}"
            return None

    while level and not stop:
        results = await asyncio.gather(*(one(r, d) for r, d in level))
        nxt: list[tuple[Rect, int]] = []
        for res in results:
            if res is None:
                continue
            if res.depth >= max_depth:
                res.split = False
            tiles.append(res)
            if on_tile:
                maybe = on_tile(res)
                if asyncio.iscoroutine(maybe):
                    await maybe
            if res.split:
                nxt.extend((child, res.depth + 1) for child in split(res.rect))
        level = nxt
    return tiles, stop

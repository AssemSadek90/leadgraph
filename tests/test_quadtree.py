import pytest

from leadgraph.config import Rect
from leadgraph.sources.quadtree import can_split, quadtree_sweep, split, tile_size_km
from leadgraph.usage import BudgetExceeded

KM = 1 / 111.32
ROOT = Rect(south=0.0, west=0.0, north=16 * KM, east=16 * KM)  # ~16 × 16 km at the equator


class FakeTileClient:
    """Full result sets (3 × 20, with tokens) for tiles whose longer side > threshold_km."""

    def __init__(self, threshold_km: float, budget: int | None = None):
        self.threshold_km = threshold_km
        self.requests = 0
        self.budget = budget

    async def search_page(self, query, rect, page_token=None):
        if self.budget is not None and self.requests >= self.budget:
            raise BudgetExceeded("places budget reached")
        self.requests += 1
        page = int(page_token or 0)
        if max(tile_size_km(rect)) > self.threshold_km:
            places = [{"id": f"{rect.key()}-{page}-{i}"} for i in range(20)]
            return {"places": places, **({"nextPageToken": str(page + 1)} if page < 2 else {})}
        return {"places": [{"id": f"{rect.key()}-{i}"} for i in range(5)]}


def test_geometry():
    w, h = tile_size_km(ROOT)
    assert w == pytest.approx(16, rel=1e-3) and h == pytest.approx(16, rel=1e-6)
    kids = split(ROOT)
    assert len(kids) == 4
    assert all(max(tile_size_km(k)) == pytest.approx(8, rel=1e-3) for k in kids)
    assert can_split(ROOT, 7.99) and not can_split(ROOT, 8.01)


async def test_recursion_stops_exactly_at_threshold():
    client = FakeTileClient(threshold_km=3.0)
    tiles, stop = await quadtree_sweep(client, "q", ROOT, min_tile_km=0.5)
    assert stop is None
    by_depth = {}
    for t in tiles:
        by_depth.setdefault(t.depth, []).append(t)
    # 16 → 8 → 4 km tiles are full and split; 2 km tiles (≤ 3 km) are not full → leaves
    assert {d: len(v) for d, v in by_depth.items()} == {0: 1, 1: 4, 2: 16, 3: 64}
    for t in tiles:
        size = max(tile_size_km(t.rect))
        assert t.split == (size > 3.0)
        assert t.capped == (size > 3.0)
    assert all(max(tile_size_km(t.rect)) <= 3.0 for t in by_depth[3])
    assert client.requests == 21 * 3 + 64  # 21 capped tiles × 3 pages + 64 single-page leaves


async def test_min_tile_size_limits_recursion():
    client = FakeTileClient(threshold_km=3.0)
    tiles, _ = await quadtree_sweep(client, "q", ROOT, min_tile_km=5.0)
    assert len(tiles) == 5  # root + 4 children of 8 km; 4 km children would be < 5 km
    leaves = [t for t in tiles if t.depth == 1]
    assert all(t.capped and not t.split for t in leaves)


async def test_eager_split_saves_pages():
    client = FakeTileClient(threshold_km=3.0)
    tiles, _ = await quadtree_sweep(client, "q", ROOT, min_tile_km=0.5, eager_split_km=10.0)
    root = tiles[0]
    assert root.eager and root.split and root.pages == 1
    assert client.requests == 1 + 20 * 3 + 64  # root costs 1 page instead of 3


async def test_budget_stop_returns_partial_tiles():
    client = FakeTileClient(threshold_km=3.0, budget=10)
    tiles, stop = await quadtree_sweep(client, "q", ROOT, min_tile_km=0.5)
    assert stop and "budget" in stop
    assert client.requests == 10 and 1 <= len(tiles) < 85

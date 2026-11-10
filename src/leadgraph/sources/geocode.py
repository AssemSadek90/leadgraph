"""City lookup by name: one Places Text Search → a CityConfig (bounding box, test area).

Hand-written configs (config/cities/*.yaml) carry postcode ranges as well; a city looked up
by name is filtered by locality only, since postcode ranges are not available from Places.
"""

from __future__ import annotations

import math
from typing import Any

from leadgraph.config import CityConfig, Rect, city_slug
from leadgraph.sources.places import PlacesClient

KM_PER_DEG_LAT = 111.32
MAX_CITY_KM = 120.0  # a state or region would be a very expensive sweep


class CityLookupError(ValueError):
    pass


def _text(v: Any) -> str:
    return (v or {}).get("text", "") if isinstance(v, dict) else str(v or "")


def square_around(lat: float, lng: float, km: float) -> Rect:
    dlat = km / 2 / KM_PER_DEG_LAT
    dlng = km / 2 / (KM_PER_DEG_LAT * math.cos(math.radians(lat)))
    return Rect(south=lat - dlat, west=lng - dlng, north=lat + dlat, east=lng + dlng)


def rect_size_km(r: Rect) -> tuple[float, float]:
    """(width, height) in km."""
    mid = math.radians((r.south + r.north) / 2)
    return ((r.east - r.west) * KM_PER_DEG_LAT * math.cos(mid),
            (r.north - r.south) * KM_PER_DEG_LAT)


def pick_city(places: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first result Google types as a locality (a city or town)."""
    return next((p for p in places if "locality" in (p.get("types") or [])), None)


def city_from_place(place: dict[str, Any], query: str, test_area_km: float = 3.0) -> CityConfig:
    vp = place.get("viewport") or {}
    low, high = vp.get("low") or {}, vp.get("high") or {}
    if not low or not high:
        raise CityLookupError(f"Google returned no bounding box for '{query}'.")
    bbox = Rect(south=low["latitude"], west=low["longitude"],
                north=high["latitude"], east=high["longitude"])
    width, height = rect_size_km(bbox)
    if max(width, height) > MAX_CITY_KM:
        raise CityLookupError(f"'{query}' covers {width:.0f} × {height:.0f} km; "
                              f"pick a city, not a region.")
    label = _text(place.get("displayName")) or query.strip()
    names = [label]
    for comp in place.get("addressComponents") or []:
        if "locality" in (comp.get("types") or []):
            names += [comp.get("longText") or "", comp.get("shortText") or ""]
    names.append(query.strip())
    loc = place.get("location") or {}
    lat = loc.get("latitude", (bbox.south + bbox.north) / 2)
    lng = loc.get("longitude", (bbox.west + bbox.east) / 2)
    return CityConfig(
        name=city_slug(label), label=label, bbox=bbox,
        test_areas={"center": square_around(lat, lng, test_area_km)},
        locality_names=list(dict.fromkeys(n for n in names if n)),
        resolved_from=place.get("formattedAddress") or label,
    )


async def resolve_city(name: str, places: PlacesClient, test_area_km: float = 3.0) -> CityConfig:
    """One Places request (Text Search Pro, cached): the city's bounding box and names."""
    if not city_slug(name):
        raise CityLookupError("Enter a city name.")
    results = await places.find_city(name)
    place = pick_city(results)
    if place is None:
        best = results[0] if results else None
        hint = (f" Best match: {best.get('formattedAddress')} ({', '.join(best.get('types', []))})."
                if best else "")
        raise CityLookupError(f"Google found no city named '{name}'.{hint}")
    return city_from_place(place, name, test_area_km)

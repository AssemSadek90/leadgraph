"""Filtering and entity resolution (pure code).

Match keys, in priority order: normalized domain → normalized phone → normalized name +
postcode. Records sharing any key end up in the same company (union-find).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import urlparse

from leadgraph.config import CityConfig
from leadgraph.schema import Candidate, Company, ExcludedRecord

# Hosts shared by many unrelated businesses: never a dedupe key or company id.
SHARED_HOSTS = {
    "facebook.com", "m.facebook.com", "instagram.com", "linkedin.com", "xing.com",
    "google.com", "sites.google.com", "business.site", "g.page", "maps.google.com",
    "linktr.ee", "twitter.com", "x.com", "youtube.com", "immobilienscout24.de",
    "immowelt.de", "t-online.de", "web.de", "gmx.de", "gmx.net", "yelp.de", "yelp.com",
    "gelbeseiten.de", "dasoertliche.de", "11880.com", "wix.com", "jimdo.com",
}

LEGAL_FORMS = [
    r"gmbh\s*&\s*co\.?\s*kg", r"ug\s*\(haftungsbeschr(?:ae|a|ä)nkt\)",
    r"haftungsbeschr(?:ae|a|ä)nkt",
    r"gmbh", r"mbh", r"ug", r"ag", r"kg", r"ohg", r"gbr", r"e\.\s?k\.", r"ek", r"e\.v\.",
    r"partg", r"co\.?", r"inh\.?",
]
_LEGAL_RE = re.compile(r"\b(?:" + "|".join(LEGAL_FORMS) + r")(?=\W|$)", re.IGNORECASE)
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


# --- normalizers ----------------------------------------------------------------------
def normalize_domain(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    host = (urlparse(url).hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host or host in SHARED_HOSTS:
        return None
    if any(host.endswith("." + h) for h in ("facebook.com", "google.com", "business.site")):
        return None
    return host


def normalize_phone(phone: str | None, country_code: str = "49") -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if phone.strip().startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = country_code + digits[1:]
    if digits.startswith(country_code + "0"):  # "+49 (0)30 ..." style
        digits = country_code + digits[len(country_code) + 1:]
    return digits if len(digits) >= 8 else None


def normalize_name(name: str) -> str:
    s = name.lower().translate(_UMLAUTS)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _LEGAL_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def company_id(domain: str | None, name: str, postcode: str | None) -> str:
    if domain:
        return domain
    digest = hashlib.sha1(f"{normalize_name(name)}|{postcode or ''}".encode()).hexdigest()
    return f"h-{digest[:12]}"


# --- filtering ------------------------------------------------------------------------
def postcode_ok(postcode: str | None, city: CityConfig) -> bool:
    if not postcode:
        return True  # unknown postcode: keep, other filters/locality decide
    pc = postcode.strip()
    if city.postcode_prefixes and not pc.startswith(tuple(city.postcode_prefixes)):
        return False
    if city.postcode_ranges:
        if not pc.isdigit():
            return False
        return any(lo <= int(pc) <= hi for lo, hi in city.postcode_ranges)
    return True


def filter_reason(c: Candidate, city: CityConfig) -> str | None:
    """Why a candidate is dropped before dedupe, or None to keep it."""
    if c.business_status == "CLOSED_PERMANENTLY":
        return "closed_permanently"
    if not postcode_ok(c.postcode, city):
        return f"outside_city_postcode:{c.postcode}"
    if c.locality and city.locality_names and c.locality not in city.locality_names:
        return f"outside_city_locality:{c.locality}"
    return None


def apply_filters(candidates: list[Candidate], city: CityConfig
                  ) -> tuple[list[Candidate], list[ExcludedRecord]]:
    kept, excluded = [], []
    for c in candidates:
        reason = filter_reason(c, city)
        if reason is None:
            kept.append(c)
        else:
            excluded.append(ExcludedRecord(
                name=c.name, reason=reason, address=c.address, postcode=c.postcode,
                website=c.website, google_place_id=c.google_place_id, source=c.source,
            ))
    return kept, excluded


# --- entity resolution ----------------------------------------------------------------
def match_keys(c: Candidate) -> list[str]:
    keys = []
    if c.google_place_id:
        keys.append(f"place:{c.google_place_id}")
    if d := normalize_domain(c.website):
        keys.append(f"domain:{d}")
    if p := normalize_phone(c.phone):
        keys.append(f"phone:{p}")
    if c.postcode and (n := normalize_name(c.name)):
        keys.append(f"name:{n}|{c.postcode.strip()}")
    return keys


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)  # keep the earliest record as root


def _merge(group: list[Candidate]) -> Company:
    # Prefer a Places record for identity fields, then earliest-seen order.
    ordered = sorted(group, key=lambda c: (c.google_place_id is None, c.round))
    first = ordered[0]

    def pick(attr: str):
        return next((getattr(c, attr) for c in ordered if getattr(c, attr) not in (None, "")), None)

    website = pick("website")
    domain = next((d for c in ordered if (d := normalize_domain(c.website))), None)
    if domain:  # prefer a website whose domain is a real company domain
        website = next(c.website for c in ordered if normalize_domain(c.website) == domain)
    postcode = pick("postcode")
    reviews = [c.google_review_count for c in group if c.google_review_count is not None]
    types: list[str] = []
    for c in ordered:
        for t in ([c.primary_type] if c.primary_type else []) + c.types:
            if t not in types:
                types.append(t)
    sources: list[str] = []
    for c in sorted(group, key=lambda c: c.round):
        if c.source not in sources:
            sources.append(c.source)
    return Company(
        id=company_id(domain, first.name, postcode),
        name=first.name,
        address=pick("address"),
        postcode=postcode,
        website=website,
        phone=pick("phone"),
        google_place_id=pick("google_place_id"),
        google_rating=pick("google_rating"),
        google_review_count=max(reviews) if reviews else None,
        google_types=types,
        discovery_sources=sources,
        first_seen_round=min(c.round for c in group),
    )


def dedupe_groups(candidates: list[Candidate]) -> list[list[Candidate]]:
    """Candidates grouped into companies (shared match key, transitive). First-seen order."""
    uf = _UnionFind(len(candidates))
    owner: dict[str, int] = {}
    for i, c in enumerate(candidates):
        for k in match_keys(c):
            if k in owner:
                uf.union(owner[k], i)
            else:
                owner[k] = i
    groups: dict[int, list[Candidate]] = {}
    for i, c in enumerate(candidates):
        groups.setdefault(uf.find(i), []).append(c)
    return [g for _, g in sorted(groups.items())]


def dedupe(candidates: list[Candidate]) -> list[Company]:
    """Merge candidates that share any match key. Output keeps first-seen order."""
    return merge_groups(dedupe_groups(candidates))


def merge_groups(groups: list[list[Candidate]]) -> list[Company]:
    companies = [_merge(g) for g in groups]
    # Two groups can still collide on id (same hashed name+postcode without keys); merge ids.
    seen: dict[str, Company] = {}
    for comp in companies:
        if comp.id in seen:
            prev = seen[comp.id]
            prev.discovery_sources += [s for s in comp.discovery_sources
                                       if s not in prev.discovery_sources]
        else:
            seen[comp.id] = comp
    return list(seen.values())


GENERIC_NAME_TOKENS = {
    "hausverwaltung", "hausverwaltungen", "immobilienverwaltung", "immobilien", "verwaltung",
    "verwaltungs", "verwaltungsgesellschaft", "weg", "miet", "mietverwaltung", "property",
    "management", "berlin", "und", "der", "die", "das", "von", "fuer", "the", "co", "service",
    "services", "gesellschaft", "real", "estate", "objektverwaltung", "grundstuecksverwaltung",
    "haus", "grundbesitz", "grundbesitzverwaltung", "hausverwaltungsgesellschaft",
    "immobilienmanagement", "verwaltungsbuero", "gmbh", "mbh",
}


def name_similarity(a: str, b: str) -> float:
    """0–1 similarity of two business names on their distinctive tokens."""
    ta, tb = set(normalize_name(a).split()), set(normalize_name(b).split())
    da, db = ta - GENERIC_NAME_TOKENS, tb - GENERIC_NAME_TOKENS
    if not da or not db:
        return 1.0 if ta == tb and ta else 0.0
    if da <= db or db <= da:  # one name's distinctive tokens inside the other's
        return min(len(da), len(db)) / max(len(da), len(db))
    return len(da & db) / len(da | db)

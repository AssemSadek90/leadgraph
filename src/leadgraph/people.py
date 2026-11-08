"""Pure helpers for contacts: email typing, name tokens, LinkedIn result matching.

P3: emails are never guessed. These helpers only classify addresses that were found on the
company's own site, and only accept LinkedIn URLs whose result title names the person.
"""

from __future__ import annotations

import re
import unicodedata

GENERIC_LOCALS = {
    "info", "kontakt", "contact", "office", "mail", "post", "service", "hallo", "hello",
    "zentrale", "empfang", "anfrage", "anfragen", "willkommen", "team", "buero", "büro",
    "welcome", "inbox", "mailbox", "general",
}
ROLE_KEYWORDS = (
    "weg", "verwaltung", "hausverwaltung", "mietverwaltung", "miete", "mieter", "vermietung",
    "buchhaltung", "abrechnung", "technik", "hausmeister", "objekt", "eigentuemer",
    "sekretariat", "assistenz", "geschaeftsfuehrung", "gf", "leitung", "personal", "jobs",
    "karriere", "bewerbung", "datenschutz", "rechnung", "rechnungen", "notfall", "support",
    "sev", "gewerbe", "immobilien", "vertrieb", "akquise", "marketing", "presse",
)
TITLES = {"dr", "prof", "dipl", "ing", "herr", "frau", "mag", "ra", "b", "a", "sc", "m"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_UML = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def fold(text: str) -> str:
    s = text.lower().translate(_UML)
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def name_tokens(name: str) -> list[str]:
    """'Dr. Max-Peter Müller' → ['max', 'peter', 'mueller'] (titles dropped)."""
    tokens = re.split(r"[\s\-.,]+", fold(name))
    return [t for t in tokens if t and t not in TITLES and t.isalpha() and len(t) > 1]


def first_last(name: str) -> tuple[str, str] | None:
    t = name_tokens(name)
    return (t[0], t[-1]) if len(t) >= 2 else None


def valid_email_syntax(email: str) -> bool:
    return bool(EMAIL_RE.fullmatch(email.strip()))


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower().strip(".")


def email_type(email: str, person_name: str | None = None) -> str:
    """generic (info@, kontakt@, office@ …) · personal (name-based) · role (weg@, verwaltung@ …)."""
    local = fold(email.split("@", 1)[0])
    parts = [p for p in re.split(r"[._\-+]", local) if p]
    if local in GENERIC_LOCALS or (parts and parts[0] in GENERIC_LOCALS and len(parts) == 1):
        return "generic"
    if person_name and (fl := first_last(person_name)):
        first, last = fl
        compact = local.replace(".", "").replace("-", "").replace("_", "")
        if last in local or compact in {first + last, first[0] + last, last + first,
                                        last + first[0], first}:
            return "personal"
    if any(p in ROLE_KEYWORDS for p in parts) or local in ROLE_KEYWORDS:
        return "role"
    # name-like: initial or first name, separator, surname (m.mueller, max.mustermann)
    if len(parts) >= 2 and all(p.isalpha() for p in parts) and len(parts[-1]) >= 3:
        return "personal"
    return "role"


def linkedin_match(url: str, title: str, person_name: str) -> bool:
    """Keep a search result only if it is a /in/ profile and its title names the person."""
    if not re.match(r"https?://([a-z]{2,3}\.)?(www\.)?linkedin\.com/in/[^/?#]+", url.strip()):
        return False
    fl = first_last(person_name)
    if not fl:
        return False
    title_f = fold(title)
    return all(re.search(rf"\b{re.escape(tok)}\b", title_f) for tok in fl)

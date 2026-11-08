"""HTML → text and links. trafilatura for main text, selectolax for full text and links."""

from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin, urlparse

import trafilatura
from selectolax.lexbor import LexborHTMLParser as HTMLParser

_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n\s*\n+")


def full_text(html: str) -> str:
    tree = HTMLParser(html)
    for tag in tree.css("script, style, noscript, svg, template"):
        tag.decompose()
    root = tree.body or tree.root
    if root is None:
        return ""
    text = root.text(separator="\n")
    text = _WS.sub(" ", text)
    return _BLANKS.sub("\n", text).strip()


def main_text(html: str, url: str | None = None) -> str:
    text = trafilatura.extract(
        html, url=url, include_comments=False, include_tables=True, favor_recall=True
    )
    return (text or "").strip()


def title(html: str) -> str | None:
    node = HTMLParser(html).css_first("title")
    return node.text(strip=True) if node else None


def links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Absolute http(s) links with their anchor text, deduplicated, in page order."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for a in HTMLParser(html).css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute, _ = urldefrag(urljoin(base_url, href))
        if urlparse(absolute).scheme not in ("http", "https") or absolute in seen:
            continue
        seen.add(absolute)
        out.append((absolute, _WS.sub(" ", a.text(strip=True))[:120]))
    return out

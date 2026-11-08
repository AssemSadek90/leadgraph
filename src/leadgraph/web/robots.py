"""robots.txt handling (RFC 9309): 4xx → allow all, 5xx/unreachable → disallow all."""

from __future__ import annotations

from urllib.robotparser import RobotFileParser


def parse_robots(status: int | None, body: str | None) -> RobotFileParser:
    rp = RobotFileParser()
    if status is not None and 200 <= status < 300 and body is not None:
        rp.parse(body.splitlines())
    elif status is not None and 400 <= status < 500:
        rp.allow_all = True
    else:
        rp.disallow_all = True
    return rp


def allowed(rp: RobotFileParser, user_agent: str, url: str) -> bool:
    return rp.can_fetch(user_agent, url)

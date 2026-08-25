#!/usr/bin/env python3
"""HTTP + robots.txt layer for the competitor watch pipeline.

Two rules are enforced here, not in the caller:

1. Every host's robots.txt is fetched at run time and parsed. A path is only
   requested after `RobotsRules.decide(path)` returns allowed. The evaluator
   lives in `_shared/robots.py` so both usecases judge identically.
2. Requests to the same process are spaced by `min_interval` seconds.

The User-Agent identifies who is calling and where to complain.
Standard library only.
"""

from __future__ import annotations

import gzip
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
from robots import RobotsRules, path_of  # noqa: E402,F401  재수출: 기존 호출부 유지

DEFAULT_UA = "QJC-research/1.0 (+https://qjc.app)"
_last_request_at = 0.0


class Fetched:
    """One HTTP response, or the error that replaced it."""

    def __init__(self, url: str, status: int | None, body: str, error: str = "") -> None:
        self.url = url
        self.status = status
        self.body = body
        self.error = error

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.error

    def __repr__(self) -> str:
        return f"<Fetched {self.url} status={self.status} bytes={len(self.body)} err={self.error}>"


def polite_sleep(min_interval: float) -> None:
    """Block until at least `min_interval` seconds have passed since the last call."""
    global _last_request_at
    wait = min_interval - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def fetch(url: str, user_agent: str = DEFAULT_UA, min_interval: float = 1.5,
          timeout: int = 30) -> Fetched:
    """One rate limited GET. Never raises; failures come back on `Fetched.error`."""
    polite_sleep(min_interval)
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip",
               "Accept": "text/html,application/xhtml+xml,application/xml"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return Fetched(resp.geturl(), resp.status, raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return Fetched(url, exc.code, "", f"HTTPError {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        return Fetched(url, None, "", f"URLError {exc.reason}")
    except Exception as exc:  # socket timeouts, decoding, DNS
        return Fetched(url, None, "", f"{type(exc).__name__} {exc}")


def load_robots(base_url: str, user_agent: str = DEFAULT_UA,
                min_interval: float = 1.5) -> tuple[RobotsRules | None, Fetched]:
    """Fetch and parse <base>/robots.txt. Returns (rules_or_None, raw response)."""
    robots_url = urllib.parse.urljoin(base_url, "/robots.txt")
    res = fetch(robots_url, user_agent=user_agent, min_interval=min_interval)
    if not res.ok:
        return None, res
    return RobotsRules(res.body, user_agent), res


def path_of(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path or "/"
    return f"{path}?{parsed.query}" if parsed.query else path


def locs_from_sitemap(xml: str) -> list[str]:
    """Every <loc> in a sitemap or sitemap index, in document order."""
    return [m.strip() for m in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml, re.S)]

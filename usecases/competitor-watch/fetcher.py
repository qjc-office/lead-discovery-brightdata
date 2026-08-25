#!/usr/bin/env python3
"""HTTP + robots.txt layer for the competitor watch pipeline.

Two rules are enforced here, not in the caller:

1. Every host's robots.txt is fetched at run time and parsed. A path is only
   requested after `RobotsRules.allows(path)` returns True.
2. Requests to the same process are spaced by `min_interval` seconds.

The User-Agent identifies who is calling and where to complain.
Standard library only.
"""

from __future__ import annotations

import gzip
import re
import time
import urllib.error
import urllib.parse
import urllib.request

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


def _pattern_to_regex(pattern: str) -> re.Pattern:
    """Translate a robots.txt path pattern ('*' wildcard, '$' end anchor) to a regex."""
    parts = []
    for ch in pattern:
        if ch == "*":
            parts.append(".*")
        elif ch == "$":
            parts.append("$")
        else:
            parts.append(re.escape(ch))
    return re.compile("^" + "".join(parts))


class RobotsRules:
    """Minimal robots.txt evaluator: agent groups, Allow/Disallow, longest match wins."""

    def __init__(self, text: str, user_agent: str) -> None:
        self.raw = text
        self.agent_token = user_agent.split("/")[0].lower()
        self.groups = self._parse(text)

    @staticmethod
    def _parse(text: str) -> dict[str, list[tuple[bool, str]]]:
        groups: dict[str, list[tuple[bool, str]]] = {}
        current: list[str] = []
        expecting_agent = True
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field = field.strip().lower()
            value = value.strip()
            if field == "user-agent":
                if not expecting_agent:
                    current = []
                    expecting_agent = True
                current.append(value.lower())
                groups.setdefault(value.lower(), [])
            elif field in ("allow", "disallow"):
                expecting_agent = False
                for agent in current or ["*"]:
                    groups.setdefault(agent, []).append((field == "allow", value))
        return groups

    def rules(self) -> list[tuple[bool, str]]:
        """Rules for our agent token, falling back to the wildcard group."""
        for name, rules in self.groups.items():
            if self.agent_token and self.agent_token in name:
                return rules
        return self.groups.get("*", [])

    def decide(self, path: str) -> tuple[bool, str]:
        """Return (allowed, reason). Longest matching pattern wins; ties favour Allow."""
        best: tuple[int, bool, str] | None = None
        for allow, pattern in self.rules():
            if pattern == "":
                continue  # 'Disallow:' with empty value means allow everything
            if _pattern_to_regex(pattern).match(path):
                score = len(pattern)
                if best is None or score > best[0] or (score == best[0] and allow):
                    best = (score, allow, pattern)
        if best is None:
            return True, "no matching rule (default allow)"
        allow, pattern = best[1], best[2]
        verb = "Allow" if allow else "Disallow"
        return allow, f"{verb}: {pattern}"


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

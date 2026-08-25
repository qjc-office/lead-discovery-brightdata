#!/usr/bin/env python3
"""robots.txt 규칙 평가기 (RFC 9309).

표준 라이브러리의 `urllib.robotparser` 를 쓰지 않는다. 그 모듈은 CPython 3.14 에서야
RFC 9309 로 재작성됐고, 3.10~3.13 에서는 아래 세 가지를 전부 무시한다.

    §2.2.2  최장 일치 우선   Allow: / + Disallow: /secret  ->  /secret 을 허용해 버림
    §2.2.3  `*` 와일드카드   Disallow: /api/*/private      ->  매칭 실패
    §2.2.3  `$` 끝 앵커      Disallow: /*.pdf$             ->  매칭 실패

셋 다 "막아야 할 것을 허용"하는 방향이라 조용히 뚫린다. macOS 기본 python3 와
Ubuntu 22.04·24.04 기본 python3 가 전부 그 구간이라 실제로 대부분의 독자가 걸린다.
그래서 판정만은 직접 구현한다. 표준 라이브러리만 쓴다는 원칙은 그대로다.
"""

from __future__ import annotations

import re

# robots.txt 파싱 상한. RFC 9309 §2.5 는 최소 500 KiB 를 파싱하라고 요구한다.
PARSE_LIMIT_BYTES = 512 * 1024


def _pattern_to_regex(pattern: str) -> re.Pattern:
    """robots.txt 경로 패턴을 정규식으로. `*` 는 임의 문자열, 끝의 `$` 는 끝 앵커."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    parts = [".*" if ch == "*" else re.escape(ch) for ch in body]
    return re.compile("^" + "".join(parts) + ("$" if anchored else ""))


class RobotsRules:
    """에이전트 그룹 · Allow/Disallow · 최장 일치 우선."""

    def __init__(self, text: str, user_agent: str) -> None:
        self.raw = text
        # RFC §2.2.1 의 product token. "QJC-research/1.0 (+url)" -> "qjc-research"
        self.agent_token = user_agent.split("/")[0].strip().lower()
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
        """우리 토큰에 맞는 그룹 하나. 없으면 `*` 폴백.

        RFC §2.2.1 은 적용 그룹이 **하나**라고 못 박는다. 명시 그룹이 잡히면 그
        그룹만 보고, 없을 때만 `*` 를 본다. 두 그룹을 겹쳐 보면(AND) 사이트가 우리를
        화이트리스트에 넣어 준 경우를 오히려 차단으로 뒤집는다.

        여러 그룹이 걸리면 가장 구체적인(토큰이 긴) 것을 고른다. 딕셔너리 순서,
        즉 robots.txt 에 적힌 순서에 판정이 좌우되면 안 되기 때문이다.
        """
        if self.agent_token:
            matched = [name for name in self.groups if name != "*" and name in self.agent_token]
            if matched:
                return self.groups[max(matched, key=len)]
        return self.groups.get("*", [])

    def decide(self, path: str) -> tuple[bool, str]:
        """(허용여부, 사유). 최장 일치 우선, 같은 길이면 Allow 우선(§2.2.2)."""
        best: tuple[int, bool, str] | None = None
        for allow, pattern in self.rules():
            if pattern == "":
                continue  # 값이 빈 Disallow 는 "전부 허용"이라 매칭 대상이 아니다
            if _pattern_to_regex(pattern).match(path):
                score = len(pattern)
                if best is None or score > best[0] or (score == best[0] and allow):
                    best = (score, allow, pattern)
        if best is None:
            return True, "해당 규칙 없음 (기본 허용)"
        allow, pattern = best[1], best[2]
        return allow, f"{'Allow' if allow else 'Disallow'}: {pattern}"


def path_of(url: str) -> str:
    """판정 대상 경로. 쿼리스트링까지 포함한다(§2.2.2 는 URI 기준으로 매칭하라고 한다)."""
    import urllib.parse
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path or "/"
    return f"{path}?{parsed.query}" if parsed.query else path

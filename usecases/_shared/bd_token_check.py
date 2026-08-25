#!/usr/bin/env python3
"""Bright Data 자격증명 상태를 확인하고 결과를 파일로 남긴다.

"키가 아직 없다"는 말을 주장이 아니라 확인 가능한 기록으로 만들기 위한 스크립트다.
토큰 값은 어디에도 출력하지 않는다. 존재 여부와 응답 코드만 기록한다.

  python3 bd_token_check.py --out ../lead-discovery/results/bd-credential-check.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROBES = [
    ("계정 상태", "https://api.brightdata.com/status"),
    ("스크래퍼 목록", "https://api.brightdata.com/datasets/v3/scrapers"),
]
ENV_NAMES = ["BRIGHTDATA_API_KEY", "BRIGHTDATA_API_TOKEN"]


def probe(url: str, token: str) -> tuple[str, str]:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")[:200]
            return str(resp.status), body
    except urllib.error.HTTPError as exc:
        return str(exc.code), exc.read().decode("utf-8", "replace")[:200]
    except Exception as exc:  # noqa: BLE001
        return "네트워크 실패", repr(exc)[:200]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Bright Data 자격증명 확인")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    lines = ["# Bright Data 자격증명 확인", "", f"확인 시각 {now}", "",
             "토큰 값은 기록하지 않는다. 환경변수 존재 여부와 API 응답만 남긴다.", "",
             "| 환경변수 | 설정 여부 | 길이 |", "|---|---|---:|"]
    token = ""
    for name in ENV_NAMES:
        v = os.environ.get(name, "")
        lines.append(f"| `{name}` | {'설정됨' if v else '없음'} | {len(v)} |")
        token = token or v

    if not token:
        lines += ["", "설정된 토큰이 없어 API 호출을 건너뛴다.",
                  "이 상태에서는 모든 Bright Data 경로가 픽스처(mock)로만 동작한다."]
        verdict = "토큰 없음"
    else:
        lines += ["", "## API 응답", "", "| 확인 항목 | HTTP | 응답 |", "|---|---|---|"]
        codes = []
        for label, url in PROBES:
            code, body = probe(url, token)
            codes.append(code)
            lines.append(f"| {label} | {code} | `{body.strip()[:120]}` |")
        if all(c == "200" for c in codes):
            verdict = "사용 가능"
        elif "401" in codes:
            verdict = "인증 실패 (만료 또는 무효)"
        else:
            verdict = "확인 불가"
        lines += ["", f"판정: **{verdict}**"]

    lines += ["", "## 이 결과가 뜻하는 것", "",
              "판정이 사용 가능이 아니면 각 파이프라인의 Bright Data 경로는 `--mock`으로만 돈다.",
              "그 경로에서 나온 행은 `data_origin` 컬럼에 mock으로 표시되며 실제 수집 결과가 아니다.",
              "공개 소스에서 나온 행은 같은 컬럼에 real로 표시된다.", ""]

    text = "\n".join(lines)
    print(text)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

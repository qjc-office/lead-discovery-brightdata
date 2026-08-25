#!/usr/bin/env python3
"""AI 교육·부트캠프 경쟁사 프로그램 수집기.

  python3 collect.py                 # 공개 페이지만 수집
  python3 collect.py --bd            # 막힌 소스는 Bright Data 어댑터(mock)로 보완
  python3 collect.py --bd --bd-live  # 같은 경로를 실제 API 키로 실행
  python3 collect.py --bd-probe      # BRIGHTDATA 토큰 상태만 확인

규칙
  - 모든 호스트의 robots.txt 를 실행 시점에 읽고, 금지 경로는 요청하지 않는다.
  - 요청 간격은 targets.json 의 min_request_interval_sec 이상.
  - 페이지에서 못 읽은 값은 빈칸으로 둔다. 추정치를 채우지 않는다.
  - 실제 수집분은 data_origin=real, Bright Data mock 픽스처는 data_origin=mock.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import fetcher
import parsers

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))  # 레포 루트의 bd_client.py 재사용

CSV_FIELDS = ["source", "brand", "program_name", "price_krw", "list_price_krw",
              "duration", "format", "cohort_status", "keywords", "url",
              "collected_at", "data_origin"]


class Logger:
    """stdout 과 run-log.txt 에 동시에 쓴다."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("a", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        line = f"{datetime.now(timezone.utc).strftime('%H:%M:%SZ')} {msg}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()


class RobotsGate:
    """authority 별로 robots.txt 를 따로 읽어 판정한다.

    robots.txt 는 scheme+host+port 단위로 존재한다(RFC 9309 §2.3). 소스 하나에
    robots 하나를 물려 두면, sitemap 인덱스의 자식이 다른 호스트(예: cdn.<host>)에
    있을 때 남의 집 규칙으로 판정하게 된다. 그래서 URL 이 들어올 때마다 그 URL 의
    authority 에 맞는 규칙을 찾는다. 읽은 robots 는 authority 별로 한 번만 받는다.
    """

    def __init__(self, cfg: dict, log: Logger) -> None:
        self.ua = cfg["user_agent"]
        self.gap = cfg["min_request_interval_sec"]
        self.log = log
        self._cache: dict[str, tuple[object, object]] = {}

    @staticmethod
    def _authority(url: str) -> str:
        parts = urllib.parse.urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def rules_for(self, url: str, source_key: str):
        base = self._authority(url)
        if base not in self._cache:
            rules, res = fetcher.load_robots(base, self.ua, self.gap)
            if rules is None:
                self.log(f"[{source_key}] {base}/robots.txt 읽기 실패 "
                         f"status={res.status} {res.error}")
            else:
                self.log(f"[{source_key}] {base}/robots.txt OK ({len(res.body)} bytes) "
                         f"rules={len(rules.rules())}")
            self._cache[base] = (rules, res)
        return self._cache[base]

    def allows(self, url: str, source_key: str) -> bool:
        rules, res = self.rules_for(url, source_key)
        path = fetcher.path_of(url)
        if rules is None:
            # 규칙을 못 읽었으면 요청하지 않는다. 이 파이프라인은 fail-closed 다.
            self.log(f"[{source_key}] robots DENY  {path}  <- robots.txt 확인 불가 "
                     f"(status={res.status})")
            return False
        ok, reason = rules.decide(path)
        self.log(f"[{source_key}] robots {'ALLOW' if ok else 'DENY '} {path}  <- {reason}")
        return ok

    def entry_failure(self, url: str, source_key: str) -> str:
        """진입 경로의 robots 를 못 읽었을 때 blocked-sources.md 에 남길 사유."""
        rules, res = self.rules_for(url, source_key)
        return "" if rules is not None else f"robots.txt 응답 실패 (status={res.status})"


def _keyword_hit(url: str, watch: list[str], decode: bool) -> bool:
    haystack = urllib.parse.unquote(url) if decode else url
    return any(kw.lower() in haystack.lower() for kw in watch)


def discover(source: dict, cfg: dict, gate: "RobotsGate", log: Logger) -> tuple[list[str], str]:
    """수집 후보 URL 목록. 두 번째 값은 실패 사유(있을 때)."""
    disc = source["discovery"]
    ua, gap = cfg["user_agent"], cfg["min_request_interval_sec"]
    urls: list[str] = []

    seeds = [disc["url"]] if disc["type"] in ("sitemap", "sitemap_index") else disc["urls"]
    for seed in seeds:
        if not gate.allows(seed, source["key"]):
            return [], f"robots 가 진입 경로를 금지: {fetcher.path_of(seed)}"
        res = fetcher.fetch(seed, ua, gap)
        log(f"[{source['key']}] GET {seed} -> {res.status} {len(res.body)} bytes {res.error}")
        if not res.ok:
            continue
        if disc["type"] == "sitemap_index":
            children = [u for u in fetcher.locs_from_sitemap(res.body)
                        if any(inc in u for inc in disc["child_include"])]
            picked = children[-disc.get("child_limit", 2):] if disc.get("child_pick") == "last" \
                else children[:disc.get("child_limit", 2)]
            for child in picked:
                # 자식 사이트맵은 다른 호스트일 수 있다. 반드시 따로 판정한다.
                if not gate.allows(child, source["key"]):
                    continue
                sub = fetcher.fetch(child, ua, gap)
                log(f"[{source['key']}] GET {child} -> {sub.status} {len(sub.body)} bytes")
                if sub.ok:
                    urls += fetcher.locs_from_sitemap(sub.body)
        elif disc["type"] == "sitemap":
            urls += fetcher.locs_from_sitemap(res.body)
        else:
            found = re.findall(r'href="(' + disc["link_pattern"] + r')"', res.body)
            urls += [urllib.parse.urljoin(source["base"], u) for u in found]
            log(f"[{source['key']}] 목록에서 추출한 링크 {len(found)}개")

    return _filter_urls(urls, disc, cfg), ""


def _filter_urls(urls: list[str], disc: dict, cfg: dict) -> list[str]:
    out, seen = [], set()
    for url in urls:
        if disc.get("url_include") and not any(inc in url for inc in disc["url_include"]):
            continue
        if any(exc in url for exc in disc.get("url_exclude", [])):
            continue
        if disc.get("url_keyword_filter") and not _keyword_hit(
                url, cfg["watch_keywords"], disc.get("url_decode", False)):
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def scrape_pages(source: dict, cfg: dict, gate: "RobotsGate", urls: list[str], log: Logger) -> list[dict]:
    parse = parsers.PARSERS[source["parser"]]
    ctx = {"watch_keywords": cfg["watch_keywords"], "brand": source["brand"]}
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for url in urls[: source.get("max_pages", 10)]:
        if not gate.allows(url, source["key"]):
            continue
        res = fetcher.fetch(url, cfg["user_agent"], cfg["min_request_interval_sec"])
        row = parse(url, res.body, ctx) if res.ok else None
        log(f"[{source['key']}] GET {url} -> {res.status} {len(res.body)} bytes "
            f"parsed={'yes' if row else 'no'} {res.error}")
        if not row:
            continue
        if source.get("filter_after_parse") and not _keyword_hit(
                f'{row["program_name"]} {row["keywords"]}', cfg["watch_keywords"], False):
            log(f"[{source['key']}] 키워드 불일치로 제외: {row['program_name']}")
            continue
        rows.append({**row, "source": source["key"], "brand": source["brand"],
                     "url": url, "collected_at": stamp, "data_origin": "real"})
    return rows


def resolve_token() -> str:
    """bd_client 는 BRIGHTDATA_API_KEY 만 보지만 이 환경의 변수명은 ..._TOKEN 이다."""
    return os.environ.get("BRIGHTDATA_API_TOKEN") or os.environ.get("BRIGHTDATA_API_KEY") or ""


def bright_data_rows(source: dict, live: bool, log: Logger) -> list[dict]:
    """robots 나 JS 렌더로 막힌 소스를 Bright Data 경로로 보완한다."""
    from bd_client import BrightDataClient, BrightDataError, LiveTransport, MockTransport

    bd = source["bright_data"]
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log(f"[{source['key']}] Bright Data 경로 시도 (mode={'live' if live else 'mock'}) "
        f"reason={bd['reason']}")
    try:
        transport = LiveTransport(resolve_token()) if live \
            else MockTransport(ROOT / "mock", bd["mock_key"])
        _, records = BrightDataClient(transport, logger=log).collect(
            dataset_id=bd["dataset_id"], inputs=bd["inputs"],
            params=bd.get("trigger_params", {}), fmt="json")
    except BrightDataError as exc:
        log(f"[{source['key']}] Bright Data ERROR {exc}")
        return []
    rows = []
    for rec in records:
        row = parsers.blank_row()
        for field, src_key in bd["field_map"].items():
            if field in row:
                row[field] = rec.get(src_key) or ""
        rows.append({**row, "source": source["key"], "brand": source["brand"],
                     "url": rec.get(bd["field_map"].get("url", "url")) or source["base"],
                     "collected_at": stamp, "data_origin": "live" if live else "mock"})
    return rows


def probe_token(log: Logger) -> None:
    """토큰이 실제로 통하는지 읽기 전용 호출 1회로 확인한다."""
    from bd_client import BrightDataError, LiveTransport

    token = resolve_token()
    if not token:
        log("[bd-probe] BRIGHTDATA_API_TOKEN / BRIGHTDATA_API_KEY 둘 다 미설정")
        return
    log(f"[bd-probe] 토큰 감지 (길이 {len(token)}자). progress 엔드포인트로 유효성 확인")
    try:
        LiveTransport(token).progress("probe_token_check")
        log("[bd-probe] 인증 통과 (2xx). live 수집 가능")
    except BrightDataError as exc:
        log(f"[bd-probe] 인증 실패: {exc}")


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_blocked(notes: list[dict], path: Path) -> None:
    lines = ["# 접근하지 못한 소스", "",
             f"생성 시각: {datetime.now(timezone.utc).isoformat(timespec='seconds')}", "",
             "| 브랜드 | 진입 경로 | 유형 | 근거 | Bright Data 가 필요한 지점 |",
             "|---|---|---|---|---|"]
    for n in notes:
        lines.append(f"| {n['brand']} | {n['entry']} | {n['kind']} | {n['evidence']} | {n['bd']} |")
    lines += ["", "유형 설명", "",
              "- robots-disallow: robots.txt 가 해당 경로를 금지. 수집하지 않는다.",
              "- js-render: robots 는 허용하지만 정적 HTML 에 데이터가 없어 렌더링이 필요하다.",
              "- fetch-error: 네트워크 또는 HTTP 오류.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv):
    ap = argparse.ArgumentParser(description="경쟁사 프로그램 수집")
    ap.add_argument("--out-dir", default=str(ROOT / "results"))
    ap.add_argument("--date", help="출력 파일 날짜 스탬프 (YYYYMMDD)")
    ap.add_argument("--bd", action="store_true", help="막힌 소스를 Bright Data 경로로 보완")
    ap.add_argument("--bd-live", action="store_true", help="Bright Data 를 실제 API 로 호출")
    ap.add_argument("--bd-probe", action="store_true", help="토큰 유효성만 확인하고 종료")
    ap.add_argument("--only", help="이 키의 소스만 실행")
    return ap.parse_args(argv)


def run_source(source: dict, cfg: dict, args, log: Logger) -> tuple[list[dict], dict | None]:
    """소스 1개를 수집한다. 반환은 (행 목록, 차단 기록 or None)."""
    log(f"--- source={source['key']} brand={source['brand']} base={source['base']}")
    gate = RobotsGate(cfg, log)
    entry = source["discovery"].get("url") or source["discovery"]["urls"][0]
    err = gate.entry_failure(entry, source["key"])
    if err:
        return [], {"brand": source["brand"], "entry": entry, "kind": "fetch-error",
                    "evidence": err, "bd": "robots 재확인 후 판단"}

    urls, block = discover(source, cfg, gate, log)
    if block:
        return [], {"brand": source["brand"], "entry": entry, "kind": "robots-disallow",
                    "evidence": block,
                    "bd": "robots 가 금지하므로 Bright Data 로도 수집하지 않는다"}
    log(f"[{source['key']}] 후보 URL {len(urls)}개 (상한 {source.get('max_pages', 10)})")

    rows = scrape_pages(source, cfg, gate, urls, log) if urls else []
    log(f"[{source['key']}] 실수집 {len(rows)}행")
    if rows:
        return rows, None

    note = {"brand": source["brand"], "entry": entry, "kind": "js-render",
            "evidence": f"robots 허용, 정적 HTML 에서 후보 {len(urls)}건 / 파싱 0건",
            "bd": source.get("bright_data", {}).get("reason", "렌더링 경로 필요")}
    if args.bd and source.get("bright_data"):
        rows = bright_data_rows(source, args.bd_live, log)
        log(f"[{source['key']}] Bright Data 경로 {len(rows)}행")
    return rows, note


def main(argv) -> int:
    args = parse_args(argv)
    cfg = json.loads((ROOT / "targets.json").read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    log = Logger(out_dir / "run-log.txt")
    stamp = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")

    log("=" * 78)
    log(f"RUN collect.py argv={' '.join(argv) or '(없음)'}")
    log(f"python={sys.version.split()[0]} UA={cfg['user_agent']} "
        f"interval={cfg['min_request_interval_sec']}s")
    probe_token(log)
    if args.bd_probe:
        return 0

    rows, notes = [], []
    for source in cfg["sources"]:
        if args.only and source["key"] != args.only:
            continue
        src_rows, note = run_source(source, cfg, args, log)
        rows += src_rows
        if note:
            notes.append(note)

    csv_path = out_dir / f"competitors_{stamp}.csv"
    write_csv(rows, csv_path)
    write_blocked(notes, out_dir / "blocked-sources.md")
    real = sum(1 for r in rows if r["data_origin"] == "real")
    priced = sum(1 for r in rows if r["data_origin"] == "real" and r["price_krw"])
    log(f"SUMMARY 총 {len(rows)}행 (real={real}, mock={len(rows) - real}), "
        f"가격 확보 {priced}행, 브랜드 {len({r['brand'] for r in rows})}개")
    log(f"WROTE {csv_path}")
    log(f"WROTE {out_dir / 'blocked-sources.md'}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

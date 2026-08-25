#!/usr/bin/env python3
"""수집한 공고를 회사 단위로 묶고 공공 데이터로 보강한다.

보강 소스
  원티드 공개 회사 API   회사 소개문과 사업자등록번호. 업종 분류의 근거 텍스트가 된다.
  국세청 사업자등록상태  계속사업자 여부와 과세유형. 실재 여부 확인용. (DATA_GO_NTS_API_KEY)
  DART 공시대상 법인 명부 등재 여부를 조직 규모의 대리 지표로 쓴다. (DART_API_KEY)

국민연금 사업장 API는 키 인증까지는 통과하나(resultCode 00) 조회 결과가 항상 0건이라
직원수 확보에 쓰지 못했다. 이 사실은 결과 파일에 그대로 남긴다.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "_shared"))

from common import Fetcher, RunLog, BROWSER_UA, read_json, write_json, stamp  # noqa: E402
from taxonomy import (classify_industry, role_signals, is_ai_native, is_si_vendor,  # noqa: E402
                      ai_tool_signals, extract_headcount)

JUMPIT_DETAIL = "https://api.jumpit.co.kr/api/position/{pid}"

WANTED_COMPANY = "https://www.wanted.co.kr/api/v1/companies/{cid}"
WANTED_COMPANY_META = "https://www.wanted.co.kr/api/chaos/companies/v1/{cid}"
NTS_STATUS = "https://api.odcloud.kr/api/nts-businessman/v1/status?serviceKey={key}"
DART_CORPCODE = "https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}"

SUFFIX = re.compile(r"(\(주\)|주식회사|㈜|\(유\)|유한회사|Inc\.?|Corp\.?|Co\.,? ?Ltd\.?|LLC)", re.I)
PAREN = re.compile(r"[（(\[][^）)\]]*[）)\]]")

# 표준 라이브러리만 쓰는 제약 때문에 defusedxml을 쓸 수 없다. 대신 파싱 전에
# DOCTYPE과 ENTITY 선언을 거부해 외부 엔티티 및 엔티티 확장 공격 경로를 막는다.
_XML_DECL_BLOCK = re.compile(rb"<!(DOCTYPE|ENTITY)", re.I)


def safe_parse_xml(data: bytes) -> ET.Element:
    if _XML_DECL_BLOCK.search(data[:20000]):
        raise ValueError("DOCTYPE 또는 ENTITY 선언이 포함된 XML은 처리하지 않는다")
    parser = ET.XMLParser()
    parser.feed(data)
    return parser.close()


def norm_name(name: str) -> str:
    return SUFFIX.sub("", name or "").replace(" ", "").strip().lower()


def name_variants(name: str) -> set[str]:
    """DART 대조용 이름 후보. 괄호 별칭을 본명과 별칭 양쪽으로 풀어 준다.

    채용 사이트 표기 "○○○(약칭)"이 DART의 "○○○"과 매칭되지 않아
    대기업이 미등재로 잡히던 문제를 막는다.
    """
    raw = (name or "").strip()
    out = {norm_name(raw), norm_name(PAREN.sub("", raw))}
    for m in re.finditer(r"[（(\[]([^）)\]]+)[）)\]]", raw):
        out.add(norm_name(m.group(1)))
    return {v for v in out if len(v) >= 2}


def group_companies(postings: list[dict]) -> dict[str, dict]:
    """공고를 회사 단위로 묶고 신호를 합산한다."""
    companies: dict[str, dict] = {}
    for p in postings:
        key = norm_name(p.get("company_name"))
        if not key:
            continue
        c = companies.setdefault(key, {
            "company_name": p["company_name"], "company_key": key,
            "sources": set(), "company_refs": set(), "postings": [],
            "ai_hits": set(), "automation_hits": set(), "research_hits": set(),
            "nondev_ai_titles": [], "min_career_seen": [], "tech_stacks": set(),
            "locations": set(), "data_origin": p.get("data_origin", "real"),
        })
        context = " ".join([p.get("job_category", ""), " ".join(p.get("tech_stacks") or [])])
        sig = role_signals(p.get("title", ""), context)
        c["sources"].add(p["source"])
        if p.get("company_ref"):
            c["company_refs"].add(p["company_ref"])
        c["postings"].append({"title": p.get("title"), "url": p.get("url"),
                              "source": p.get("source"), "posting_id": p.get("posting_id")})
        c["ai_hits"].update(sig["ai"])
        c["automation_hits"].update(sig["automation"])
        c["research_hits"].update(sig["research"])
        if sig["nondev"] and sig["ai"]:
            c["nondev_ai_titles"].append(p.get("title"))
        if p.get("min_career") is not None:
            c["min_career_seen"].append(p["min_career"])
        c["tech_stacks"].update(p.get("tech_stacks") or [])
        c["locations"].update(p.get("locations") or [])
        if p.get("data_origin") == "mock":
            c["data_origin"] = "mock"
    return companies


def load_dart(log: RunLog) -> set[str]:
    """DART 공시대상 법인 명부. 등재 여부를 조직 규모의 대리 지표로 쓴다."""
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        log("[dart] DART_API_KEY 없음, 규모 추정 생략")
        return set()
    cache = ROOT / "results" / ".dart_corp_names.json"
    if cache.exists():
        names = set(read_json(cache, []))
        log(f"[dart] 캐시에서 {len(names)}건 로드")
        return names
    try:
        raw = urllib.request.urlopen(
            DART_CORPCODE.format(key=urllib.parse.quote(key)), timeout=180).read()
        z = zipfile.ZipFile(io.BytesIO(raw))
        root = safe_parse_xml(z.read(z.namelist()[0]))
        names = {norm_name(e.findtext("corp_name")) for e in root.findall("list")}
        names.discard("")
        write_json(cache, sorted(names))
        log(f"[dart] 공시대상 법인 {len(names)}건 확보")
        return names
    except Exception as exc:  # noqa: BLE001
        log(f"[dart] 실패: {repr(exc)[:150]}")
        return set()


NTS_CHUNK = 20  # 100건 묶음은 503을 돌려줘 20건으로 낮췄다 (2026-08-04 실측)


def nts_status(fetch: Fetcher, log: RunLog, bnos: list[str]) -> dict[str, dict]:
    """국세청 사업자등록상태 조회. 묶음 크기를 넘기면 503이 나므로 작게 나눠 보낸다."""
    key = os.environ.get("DATA_GO_NTS_API_KEY", "")
    if not key or not bnos:
        log("[nts] 조회 생략 (키 없음 또는 대상 없음)")
        return {}
    cache_path = ROOT / "results" / ".nts_cache.json"
    out: dict[str, dict] = read_json(cache_path, {}) or {}
    todo = [b for b in bnos if b not in out]
    log(f"[nts] 대상 {len(bnos)}건 중 캐시 {len(bnos)-len(todo)}건, 신규 조회 {len(todo)}건")
    for i in range(0, len(todo), NTS_CHUNK):
        chunk = todo[i:i + NTS_CHUNK]
        n = i // NTS_CHUNK + 1
        code, data = 0, None
        for attempt in range(4):
            code, data = fetch.post_json(NTS_STATUS.format(key=key), {"b_no": chunk})
            if code == 200 and isinstance(data, dict):
                break
            log(f"[nts] batch {n} HTTP {code}, {3*(attempt+1)}초 후 재시도")
            time.sleep(3 * (attempt + 1))
        if code != 200 or not isinstance(data, dict):
            log(f"[nts] batch {n} 재시도 소진, 건너뜀")
            continue
        for row in data.get("data", []):
            if not row.get("b_stt"):
                continue  # 미등록 번호는 캐시에 넣지 않는다
            out[row.get("b_no")] = {
                "b_stt": row.get("b_stt"), "tax_type": row.get("tax_type") or "",
            }
        write_json(cache_path, out)
        log(f"[nts] batch {n}: 요청 {len(chunk)}건, 매칭 {data.get('match_cnt')}건")
    return out


def fetch_company_meta(fetch: Fetcher, log: RunLog, companies: dict, targets: list[str]) -> None:
    """원티드 회사 API로 소개문과 사업자등록번호를 채운다. 조회분은 캐시에 남긴다."""
    cache_path = ROOT / "results" / ".company_meta_cache.json"
    cache = read_json(cache_path, {}) or {}
    done, fetched = 0, 0
    for key in targets:
        c = companies[key]
        refs = sorted(c["company_refs"])
        if not refs:
            continue
        cid = refs[0]
        hit = cache.get(cid)
        if hit is None:
            hit = {}
            code, meta = fetch.get_json(WANTED_COMPANY_META.format(cid=cid))
            if code == 200 and isinstance(meta, dict):
                comp = meta.get("company") or {}
                hit["biz_reg_no"] = comp.get("registration_number") or ""
                hit["homepage"] = comp.get("link") or ""
            code, info = fetch.get_json(WANTED_COMPANY.format(cid=cid))
            if code == 200 and isinstance(info, dict):
                hit["company_intro"] = (info.get("info") or "")[:2000]
            cache[cid] = hit
            fetched += 1
            if fetched % 25 == 0:
                write_json(cache_path, cache)
                log(f"[company] 신규 조회 {fetched}건 (캐시 저장)")
        c["biz_reg_no"] = hit.get("biz_reg_no", "")
        c["homepage"] = hit.get("homepage", "")
        c["company_intro"] = hit.get("company_intro", "")
        done += 1
    write_json(cache_path, cache)
    log(f"[company] 회사 상세 {done}건 확보 (신규 조회 {fetched}건, 캐시 재사용 {done-fetched}건)")


def fetch_jumpit_detail(fetch: Fetcher, log: RunLog, companies: dict, targets: list[str]) -> None:
    """점핏 공고 상세에서 회사 소개, 임직원 수, 요구 도구를 가져온다.

    목록 API에는 회사 소개가 없어 업종 판정이 안 되고 규모도 알 수 없다.
    상세 API의 serviceInfo에 회사 소개와 임직원 수가, 자격 요건에 사용 도구가 적혀 있다.
    회사당 공고 하나만 조회한다.
    """
    cache_path = ROOT / "results" / ".jumpit_detail_cache.json"
    cache = read_json(cache_path, {}) or {}
    done, fetched = 0, 0
    for key in targets:
        c = companies[key]
        jp = next((p for p in c["postings"]
                   if p.get("source") == "jumpit" and p.get("posting_id")), None)
        if not jp:
            continue
        pid = jp["posting_id"]
        hit = cache.get(pid)
        if hit is None:
            code, data = fetch.get_json(JUMPIT_DETAIL.format(pid=pid))
            res = (data or {}).get("result") if isinstance(data, dict) else None
            hit = {}
            if code == 200 and isinstance(res, dict):
                hit = {
                    "service_info": (res.get("serviceInfo") or "")[:2500],
                    "requirements": " ".join(str(res.get(k) or "") for k in
                                             ("qualifications", "preferredRequirements",
                                              "responsibility"))[:3000],
                    "company_url": res.get("companyUrl") or "",
                }
            cache[pid] = hit
            fetched += 1
            if fetched % 30 == 0:
                write_json(cache_path, cache)
                log(f"[jumpit-detail] 신규 조회 {fetched}건 (캐시 저장)")
        if hit.get("service_info"):
            # 원티드 소개문이 이미 있어도 점핏 소개문을 버리지 않는다. 임직원 수처럼
            # 한쪽에만 적혀 있는 정보가 있다.
            c["service_info"] = hit["service_info"]
            if not c.get("company_intro"):
                c["company_intro"] = hit["service_info"]
        if hit.get("company_url") and not c.get("homepage"):
            c["homepage"] = hit["company_url"]
        c["jd_text"] = (c.get("jd_text", "") + " " + hit.get("requirements", "")).strip()
        done += 1
    write_json(cache_path, cache)
    log(f"[jumpit-detail] 상세 {done}건 확보 (신규 {fetched}건, 캐시 {done-fetched}건)")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="공고 회사 단위 집계와 공공데이터 보강")
    ap.add_argument("--raw", default="")
    ap.add_argument("--max-enrich", type=int, default=200,
                    help="회사 상세를 조회할 최대 기업 수")
    ap.add_argument("--out-dir", default=str(ROOT / "results"))
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    log = RunLog(out_dir / "run-log.txt")
    raw_path = Path(args.raw) if args.raw else out_dir / f"raw_postings_{stamp()}.json"
    postings = read_json(raw_path, [])
    if not postings:
        log(f"[enrich] 원본 공고 없음: {raw_path}")
        log.close()
        return 3
    log(f"[enrich] 공고 {len(postings)}건 로드")

    companies = group_companies(postings)
    log(f"[enrich] 회사 {len(companies)}곳으로 집계")

    # AI 또는 자동화 신호가 하나라도 있는 회사만 보강 대상으로 좁힌다.
    candidates = [k for k, c in companies.items()
                  if c["ai_hits"] or c["automation_hits"]]
    log(f"[enrich] AI 또는 자동화 신호 보유 회사 {len(candidates)}곳")

    ranked = sorted(candidates,
                    key=lambda k: (len(companies[k]["ai_hits"]) + len(companies[k]["automation_hits"]),
                                   len(companies[k]["postings"])), reverse=True)
    targets = ranked[:args.max_enrich]

    browser = Fetcher(min_interval=1.0, ua=BROWSER_UA)
    fetch_company_meta(browser, log, companies, targets)
    fetch_jumpit_detail(Fetcher(min_interval=1.0), log, companies, targets)

    dart_names = load_dart(log)
    api = Fetcher(min_interval=1.5)  # odcloud는 짧은 간격에 503을 돌려준다
    bnos = [companies[k].get("biz_reg_no") for k in targets if companies[k].get("biz_reg_no")]
    nts = nts_status(api, log, sorted(set(bnos)))

    result = []
    for key in ranked:
        c = companies[key]
        intro = c.get("company_intro", "")
        svc = c.get("service_info", "")
        jd = c.get("jd_text", "")
        title_blob = " ".join(p["title"] or "" for p in c["postings"])
        industry = classify_industry(
            company_name=c["company_name"], intro=intro or svc,
            extra=f"{title_blob} {svc if svc != intro else ''}")
        headcount = extract_headcount(intro) or extract_headcount(svc)
        tools = ai_tool_signals(f"{jd} {title_blob}")
        bno = c.get("biz_reg_no", "")
        st = nts.get(bno, {})
        listed, dart_conf = None, ""
        if dart_names:
            matched = name_variants(c["company_name"]) & dart_names
            listed = bool(matched)
            if listed:
                # 짧은 상호는 동명이인 법인과 충돌할 수 있다. 확정으로 쓰지 않는다.
                dart_conf = "낮음 (짧은 상호, 동명 법인 가능)" if min(
                    len(m) for m in matched) < 4 else "보통"
        result.append({
            "company_name": c["company_name"], "company_key": key,
            "industry": industry,
            "sources": sorted(c["sources"]),
            "posting_count": len(c["postings"]),
            "ai_hits": sorted(c["ai_hits"]), "automation_hits": sorted(c["automation_hits"]),
            "research_hits": sorted(c["research_hits"]),
            "nondev_ai_titles": c["nondev_ai_titles"],
            "min_career_min": min(c["min_career_seen"]) if c["min_career_seen"] else None,
            "tech_stacks": sorted(c["tech_stacks"])[:15],
            "locations": sorted(c["locations"])[:4],
            "biz_reg_no_present": bool(bno),
            "ai_native_evidence": is_ai_native(c["company_name"], intro),
            "si_vendor_evidence": is_si_vendor(c["company_name"], intro),
            "ai_tool_signals": tools,
            "headcount_stated": headcount,
            "nts_status": st.get("b_stt", ""), "nts_tax_type": st.get("tax_type", ""),
            "dart_listed": listed, "dart_match_confidence": dart_conf,
            "homepage": c.get("homepage", ""),
            "sample_postings": c["postings"][:4],
            "enriched": key in targets,
            "data_origin": c["data_origin"],
        })

    path = out_dir / f"companies_{stamp()}.json"
    write_json(path, result)
    log(f"[enrich] {len(result)}곳 저장 -> {path}")
    log(f"[enrich] 국세청 상태 확인 {sum(1 for r in result if r['nts_status'])}곳, "
        f"DART 등재 {sum(1 for r in result if r['dart_listed'])}곳")
    log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

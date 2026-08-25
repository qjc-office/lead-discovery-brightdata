#!/usr/bin/env python3
"""Extractors for the competitor watch pipeline, one per extraction technique.

Each parser takes (url, html, ctx) and returns a row dict or None.
A field that cannot be read from the page is left empty. Nothing is estimated,
inferred from a sibling page, or filled in from memory.

Pick a parser in targets.json by how the target page exposes its data:

    jsonld_course     schema.org Course in <script type="application/ld+json">
    jsonld_plus_text  the same, plus 본문 텍스트로 정가·형태·기간을 보강
    next_data         Next.js 사이트의 __NEXT_DATA__ JSON
    text_block        구조화 데이터가 없어 본문 텍스트를 정규식으로 훑는 경우
    generic           구조를 모르는 사이트. schema.org Course 만 신뢰한다

Row keys: program_name, price_krw, list_price_krw, duration, format,
          cohort_status, keywords
"""

from __future__ import annotations

import json
import re

ROW_KEYS = ("program_name", "price_krw", "list_price_krw", "duration",
            "format", "cohort_status", "keywords")


def blank_row() -> dict:
    return {k: "" for k in ROW_KEYS}


def to_text(html: str) -> str:
    """Strip script/style, turn tags into '|' separators, collapse whitespace."""
    body = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    body = re.sub(r"<style.*?</style>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    body = re.sub(r"<[^>]+>", "|", body)
    body = body.replace("&amp;", "&").replace("&nbsp;", " ").replace("&#39;", "'")
    body = re.sub(r"[ \t\r\n]+", " ", body)
    return re.sub(r"(\s*\|\s*)+", "|", body)


def page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def json_ld(html: str) -> list[dict]:
    """Every parseable application/ld+json block, flattened one level."""
    out: list[dict] = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                         html, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return [d for d in out if isinstance(d, dict)]


def next_data(html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def to_won(value) -> str:
    """Normalise a price to a bare integer string. Unusable input becomes ''."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        return str(int(value))
    digits = re.sub(r"[^\d]", "", str(value))
    return digits or ""


def offer_price(course: dict) -> tuple[str, str]:
    """(price, currency) from a schema.org Course offers field."""
    offers = course.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return "", ""
    return to_won(offers.get("price")), str(offers.get("priceCurrency") or "")


def matched_keywords(text: str, watch: list[str], limit: int = 6) -> str:
    hits = []
    low = text.lower()
    for kw in watch:
        if kw.lower() in low and kw not in hits:
            hits.append(kw)
        if len(hits) >= limit:
            break
    return ",".join(hits)


_DURATION_PATTERNS = [
    (r"전체\|?\s*(\d+)\s*개\s*[∙·]\s*\(\s*(\d+)\s*시간", "{0}개 강의 {1}시간"),
    (r"약\s*\|?\s*(\d+)\s*시간", "약 {0}시간"),
    (r"(\d+)\s*파트\s*[∙·]\s*(\d+)\s*클립", "{0}파트 {1}클립"),
    (r"총\s*\|?\s*(\d+)\s*\|?\s*시간\s*\|?\s*(\d+)\s*\|?\s*분", "{0}시간 {1}분"),
    (r"\|\s*(\d+)\s*\|?\s*회차", "총 {0}회차"),
    (r"(\d+)\s*\|?\s*주\s*\|?\s*(?:과정|완성|코스)", "{0}주"),
]


def _duration_from_text(text: str) -> str:
    for pattern, template in _DURATION_PATTERNS:
        m = re.search(pattern, text)
        if m:
            return template.format(*m.groups())
    return ""


def _list_and_sale_price(text: str) -> tuple[str, str]:
    """(정가, 판매가). 상품 헤더 블록 안에서만 읽어 본문 중간의 패키지 광고와 섞이지 않게 한다."""
    start = text.find("권장 소비자 가격")
    if start < 0:
        return "", ""
    block = text[start:start + 220]
    m_list = re.search(r"권장 소비자 가격\|*\s*([\d][\d,]{2,})", block)
    sale_at = block.find("할인 판매가")
    m_sale = re.search(r"([\d][\d,]{2,})\s*\|?\s*원", block[sale_at:]) if sale_at >= 0 else None
    list_price = to_won(m_list.group(1)) if m_list else ""
    sale_price = to_won(m_sale.group(1)) if m_sale else ""
    if list_price and sale_price and int(sale_price) > int(list_price):
        return list_price, ""  # 정가보다 비싼 '판매가'는 다른 상품의 값이다
    return list_price, sale_price


def parse_text_block(url: str, html: str, ctx: dict) -> dict | None:
    """구조화 데이터가 없는 페이지. <title> 과 본문 텍스트만으로 채운다."""
    text = to_text(html)
    row = blank_row()
    title = page_title(html)
    brand = str(ctx.get("brand") or "").strip()
    if brand:
        # "강의명 | 브랜드" 꼴의 꼬리표를 떼어 낸다. 브랜드명은 targets.json 에서 온다.
        title = re.sub(rf"\s*\|\s*{re.escape(brand)}\s*$", "", title)
    row["program_name"] = title
    if not row["program_name"]:
        return None
    row["list_price_krw"], row["price_krw"] = _list_and_sale_price(text)
    row["duration"] = _duration_from_text(text)
    row["format"] = "오프라인" if ("_camp_" in url or "오프라인" in row["program_name"]) else "온라인"
    if "평생소장" in text:
        row["cohort_status"] = "상시(평생소장)"
    elif "조기 마감" in text or "마감되었습니다" in text:
        row["cohort_status"] = "마감"
    m_badge = re.search(r"\[([^\]]*(?:예약|모집|마감)[^\]]*)\]", row["program_name"])
    if m_badge:
        row["cohort_status"] = m_badge.group(1).strip()
    tags = re.findall(r"\|#\s*([^|]{1,20})(?=\|)", text)[:6]
    row["keywords"] = ",".join(t.strip() for t in tags if t.strip()) or \
        matched_keywords(row["program_name"], ctx["watch_keywords"])
    return row


def parse_jsonld_course(url: str, html: str, ctx: dict) -> dict | None:
    """schema.org Course 만 읽는다. VOD 처럼 상시 판매되는 상품에 맞는다."""
    courses = [d for d in json_ld(html) if d.get("@type") == "Course"]
    if not courses:
        return None
    course = courses[0]
    row = blank_row()
    row["program_name"] = str(course.get("name") or "").strip()
    if not row["program_name"]:
        return None
    price, currency = offer_price(course)
    row["price_krw"] = price if currency in ("KRW", "") else ""
    row["duration"] = _duration_from_text(to_text(html))
    row["format"] = "온라인"
    row["cohort_status"] = "상시(VOD)"
    row["keywords"] = matched_keywords(
        f'{row["program_name"]} {course.get("description", "")}', ctx["watch_keywords"])
    return row


def parse_next_data(url: str, html: str, ctx: dict) -> dict | None:
    """Next.js 사이트. __NEXT_DATA__ 안에 상품 객체가 통째로 실려 있는 경우."""
    props = next_data(html).get("props", {}).get("pageProps", {})
    detail = props.get("productDetail") or {}
    meta = props.get("pdpMeta") or {}
    name = detail.get("productName") or meta.get("og_title") or ""
    if not name:
        return None
    row = blank_row()
    row["program_name"] = str(name).strip()
    row["price_krw"] = to_won(detail.get("salePrice"))
    row["list_price_krw"] = to_won(detail.get("netPrice"))
    days = detail.get("durationDays")
    row["duration"] = f"{days}일" if days else ""
    row["format"] = "온라인"
    row["cohort_status"] = "상시"
    # 이 사이트는 일부 상품에서 meta 블록이 다른 상품 것으로 남아 있다.
    # og_title 이 productName 과 같을 때만 meta_keywords 를 신뢰한다.
    meta_matches = str(meta.get("og_title") or "").strip() == row["program_name"]
    tags = [t.strip() for t in str(meta.get("meta_keywords") or "").split(",") if t.strip()]
    row["keywords"] = ",".join(tags[:6]) if (meta_matches and tags) else \
        matched_keywords(row["program_name"], ctx["watch_keywords"])
    return row


def parse_jsonld_plus_text(url: str, html: str, ctx: dict) -> dict | None:
    """schema.org Course 로 뼈대를 잡고, 정가·형태·기간·모집상태는 본문에서 보강한다."""
    courses = [d for d in json_ld(html) if d.get("@type") == "Course"]
    if not courses:
        return None
    course = courses[0]
    text = to_text(html)
    row = blank_row()
    row["program_name"] = str(course.get("name") or "").strip()
    if not row["program_name"]:
        return None
    price, currency = offer_price(course)
    row["price_krw"] = price if currency in ("KRW", "") else ""
    amounts = [int(a.replace(",", "")) for a in re.findall(r"₩\s*\|?([\d,]{5,})", text)]
    if amounts and row["price_krw"]:
        top = max(amounts)
        row["list_price_krw"] = str(top) if top > int(row["price_krw"]) else ""
    m_fmt = re.search(r"클래스\|(온라인|오프라인)", text)
    row["format"] = m_fmt.group(1) if m_fmt else ""
    row["duration"] = _duration_from_text(text)
    if "모집중" in text:
        row["cohort_status"] = "모집중"
    elif "모집마감" in text or "마감" in text:
        row["cohort_status"] = "마감"
    row["keywords"] = matched_keywords(
        f'{row["program_name"]} {course.get("description", "")}', ctx["watch_keywords"])
    return row


def parse_generic(url: str, html: str, ctx: dict) -> dict | None:
    """Fallback for sites with no known structure. Only trusts schema.org Course."""
    courses = [d for d in json_ld(html) if d.get("@type") == "Course"]
    if not courses:
        return None
    course = courses[0]
    row = blank_row()
    row["program_name"] = str(course.get("name") or "").strip()
    if not row["program_name"]:
        return None
    price, currency = offer_price(course)
    row["price_krw"] = price if currency in ("KRW", "") else ""
    row["keywords"] = matched_keywords(row["program_name"], ctx["watch_keywords"])
    return row


PARSERS = {
    "text_block": parse_text_block,
    "jsonld_course": parse_jsonld_course,
    "jsonld_plus_text": parse_jsonld_plus_text,
    "next_data": parse_next_data,
    "generic": parse_generic,
}

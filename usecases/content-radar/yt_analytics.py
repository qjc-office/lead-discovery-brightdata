"""Own channel truth from the YouTube Analytics API.

The public yt-dlp path sees what everyone sees. This module reads what only the
channel owner can read: which of our own videos actually held attention. It is
optional. When the OAuth variables are missing or the call fails, the radar
still runs on public metrics alone and the log says so.

Credentials come from the environment only:
  YOUTUBE_QJC_CLIENT_ID / YOUTUBE_QJC_CLIENT_SECRET / YOUTUBE_QJC_REFRESH_TOKEN

Endpoints:
  POST https://oauth2.googleapis.com/token                      (refresh grant)
  GET  https://youtubeanalytics.googleapis.com/v2/reports       (video metrics)
  GET  https://www.googleapis.com/youtube/v3/videos             (titles, dates)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

FIELDS = ["video_id", "title", "views", "estimated_minutes_watched",
          "average_view_seconds", "published_at", "data_origin"]


def _access_token(log) -> str:
    cid = os.environ.get("YOUTUBE_QJC_CLIENT_ID", "")
    secret = os.environ.get("YOUTUBE_QJC_CLIENT_SECRET", "")
    refresh = os.environ.get("YOUTUBE_QJC_REFRESH_TOKEN", "")
    if not (cid and secret and refresh):
        log("[own] skipped: YOUTUBE_QJC_* environment variables incomplete")
        return ""
    body = urllib.parse.urlencode({"client_id": cid, "client_secret": secret,
                                   "refresh_token": refresh,
                                   "grant_type": "refresh_token"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
                "https://oauth2.googleapis.com/token", data=body), timeout=30) as resp:
            return json.loads(resp.read())["access_token"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        log(f"[own] token refresh failed: {exc}")
        return ""


def _report_rows(token: str, days: int, top: int, log) -> list[dict]:
    query = urllib.parse.urlencode({
        "ids": "channel==MINE",
        "startDate": (date.today() - timedelta(days=days)).strftime("%Y-%m-%d"),
        "endDate": date.today().strftime("%Y-%m-%d"),
        "metrics": "views,estimatedMinutesWatched,averageViewDuration",
        "dimensions": "video", "sort": "-views", "maxResults": str(top)})
    url = f"https://youtubeanalytics.googleapis.com/v2/reports?{query}"
    try:
        with urllib.request.urlopen(urllib.request.Request(
                url, headers={"Authorization": f"Bearer {token}"}), timeout=45) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        log(f"[own] analytics HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:160]}")
        return []
    except urllib.error.URLError as exc:
        log(f"[own] analytics network failure: {exc.reason}")
        return []
    return [{"video_id": row[0], "title": "", "views": int(row[1]),
             "estimated_minutes_watched": int(row[2]), "average_view_seconds": int(row[3]),
             "published_at": "", "data_origin": "youtube-analytics-api:own-channel"}
            for row in payload.get("rows") or []]


def _attach_titles(rows: list[dict], token: str, log) -> None:
    """One Data API call fills titles and publish dates for up to 50 ids."""
    query = urllib.parse.urlencode({"part": "snippet",
                                    "id": ",".join(r["video_id"] for r in rows)})
    try:
        with urllib.request.urlopen(urllib.request.Request(
                f"https://www.googleapis.com/youtube/v3/videos?{query}",
                headers={"Authorization": f"Bearer {token}"}), timeout=45) as resp:
            items = json.loads(resp.read()).get("items") or []
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        log(f"[own] title lookup failed: {exc}")
        return
    snippets = {item["id"]: item.get("snippet", {}) for item in items}
    for row in rows:
        snippet = snippets.get(row["video_id"], {})
        row["title"] = snippet.get("title", "")
        row["published_at"] = (snippet.get("publishedAt") or "")[:10]


def own_channel_rows(days: int = 180, top: int = 25, log=print) -> list[dict]:
    token = _access_token(log)
    if not token:
        return []
    rows = _report_rows(token, days, top, log)
    if rows:
        _attach_titles(rows, token, log)
    log(f"[own] YouTube Analytics API returned {len(rows)} video row(s) for the last {days} days")
    return rows

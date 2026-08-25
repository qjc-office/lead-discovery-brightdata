"""Bright Data Web Scraper API client (async trigger, poll, download).

Endpoints implemented here follow the official Bright Data documentation:

  POST /datasets/v3/trigger              https://docs.brightdata.com/api-reference/rest-api/scraper/asynchronous-requests
  GET  /datasets/v3/progress/{snap_id}   https://docs.brightdata.com/api-reference/scrapers/management-apis/monitor-progress
  GET  /datasets/v3/snapshot/{snap_id}   https://docs.brightdata.com/api-reference/scrapers/delivery-apis/download-snapshot
  GET  /datasets/v3/snapshot/{id}/parts  https://docs.brightdata.com/api-reference/scrapers/management-apis/get-snapshot-delivery-parts
  POST /datasets/v3/scrape (sync, 1 min) https://docs.brightdata.com/api-reference/scrapers/synchronous-requests
  Auth: Authorization: Bearer <API_KEY>  https://docs.brightdata.com/api-reference/authentication

Two transports share one interface:
  * LiveTransport  -> real HTTPS calls to api.brightdata.com (needs BRIGHTDATA_API_KEY)
  * MockTransport  -> replays fixture files in mock/ so the pipeline runs end to end
                      with no API key. Mock records are synthetic and clearly labelled.

Standard library only. No pip install required.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API_BASE = "https://api.brightdata.com"
TRIGGER_PATH = "/datasets/v3/trigger"
PROGRESS_PATH = "/datasets/v3/progress"
SNAPSHOT_PATH = "/datasets/v3/snapshot"
TERMINAL_OK = "ready"
TERMINAL_FAIL = "failed"


class BrightDataError(RuntimeError):
    """Any failure while talking to the Bright Data API."""


def _request(method: str, url: str, token: str, body: Any = None, timeout: int = 60) -> Any:
    """One HTTPS round trip returning parsed JSON (or raw text when not JSON)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        if exc.code == 401:
            raise BrightDataError(
                "401 Unauthorized. Check BRIGHTDATA_API_KEY "
                "(https://brightdata.com/cp/setting/users)."
            ) from exc
        if exc.code in (402, 403) or "credit" in detail.lower() or "insufficient" in detail.lower():
            raise BrightDataError(
                f"HTTP {exc.code} on {method} {url}. This usually means the Bright Data "
                f"account has run out of credits, or the API key does not have access to "
                f"this dataset. Check your plan and balance: "
                f"https://brightdata.com/cp/billing  Detail: {detail}"
            ) from exc
        raise BrightDataError(f"HTTP {exc.code} on {method} {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BrightDataError(
            f"Network failure on {method} {url}: {exc.reason}. "
            f"Check your internet connection, or try again in a moment."
        ) from exc
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


class LiveTransport:
    """Real Bright Data API calls."""

    mode = "live"

    def __init__(self, token: str) -> None:
        if not token:
            raise BrightDataError(
                "BRIGHTDATA_API_KEY is not set. Export it, or run with --mock to use fixtures."
            )
        self.token = token

    def trigger(self, dataset_id: str, inputs: list[dict], params: dict) -> str:
        query = {"dataset_id": dataset_id, **{k: v for k, v in params.items() if v not in (None, "")}}
        url = f"{API_BASE}{TRIGGER_PATH}?{urllib.parse.urlencode(query)}"
        # Body of /datasets/v3/trigger is a bare array of input objects.
        payload = _request("POST", url, self.token, body=inputs)
        snapshot_id = (payload or {}).get("snapshot_id")
        if not snapshot_id:
            raise BrightDataError(f"trigger returned no snapshot_id: {payload!r}")
        return snapshot_id

    def progress(self, snapshot_id: str) -> dict:
        url = f"{API_BASE}{PROGRESS_PATH}/{urllib.parse.quote(snapshot_id)}"
        return _request("GET", url, self.token) or {}

    def download(self, snapshot_id: str, fmt: str, batch_size: int | None, part: int | None) -> list[dict]:
        query: dict[str, Any] = {"format": fmt}
        if batch_size:
            query["batch_size"] = batch_size
            query["part"] = part or 1
        url = f"{API_BASE}{SNAPSHOT_PATH}/{urllib.parse.quote(snapshot_id)}?{urllib.parse.urlencode(query)}"
        payload = _request("GET", url, self.token, timeout=180)
        return _coerce_records(payload)

    def parts(self, snapshot_id: str, batch_size: int | None = None) -> int:
        # The parts endpoint rejects the request without batch_size (HTTP 400).
        # https://docs.brightdata.com/api-reference/scrapers/management-apis/get-snapshot-delivery-parts
        url = f"{API_BASE}{SNAPSHOT_PATH}/{urllib.parse.quote(snapshot_id)}/parts"
        if batch_size:
            url = f"{url}?{urllib.parse.urlencode({'batch_size': batch_size})}"
        payload = _request("GET", url, self.token) or {}
        return int(payload.get("parts") or payload.get("total_parts") or 1)


class MockTransport:
    """Replays fixtures so the full pipeline runs without an API key.

    Fixture files live in mock/<dataset_key>_part<N>.json and hold SYNTHETIC
    records. They are not Bright Data output and must never be presented as
    real collection results.
    """

    mode = "mock"

    def __init__(self, mock_dir: Path, dataset_key: str, poll_steps: int = 2) -> None:
        self.mock_dir = mock_dir
        self.dataset_key = dataset_key
        self.poll_steps = poll_steps
        self._polls = 0

    def trigger(self, dataset_id: str, inputs: list[dict], params: dict) -> str:
        if not self._part_files():
            raise BrightDataError(f"No mock fixtures for '{self.dataset_key}' in {self.mock_dir}")
        self._polls = 0
        return f"s_mock_{self.dataset_key}"

    def progress(self, snapshot_id: str) -> dict:
        # Mirrors the real status ladder: starting -> running -> ready.
        ladder = ["starting"] + ["running"] * self.poll_steps + [TERMINAL_OK]
        status = ladder[min(self._polls, len(ladder) - 1)]
        self._polls += 1
        return {"snapshot_id": snapshot_id, "dataset_id": "mock", "status": status}

    def download(self, snapshot_id: str, fmt: str, batch_size: int | None, part: int | None) -> list[dict]:
        files = self._part_files()
        idx = (part or 1) - 1
        if idx >= len(files):
            return []
        return _coerce_records(json.loads(files[idx].read_text(encoding="utf-8")))

    def parts(self, snapshot_id: str, batch_size: int | None = None) -> int:
        return max(1, len(self._part_files()))

    def _part_files(self) -> list[Path]:
        return sorted(self.mock_dir.glob(f"{self.dataset_key}_part*.json"))


def _coerce_records(payload: Any) -> list[dict]:
    """Accept a JSON array, a single object, or NDJSON text."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "records"):
            if isinstance(payload.get(key), list):
                return [r for r in payload[key] if isinstance(r, dict)]
        return [payload]
    if isinstance(payload, str):
        out = []
        for line in payload.splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return [r for r in out if isinstance(r, dict)]
    return []


class BrightDataClient:
    """Trigger a collection, wait for the snapshot, download every part."""

    def __init__(self, transport: LiveTransport | MockTransport, logger=print) -> None:
        self.transport = transport
        self.log = logger

    @property
    def mode(self) -> str:
        return self.transport.mode

    @classmethod
    def from_env(cls, mock: bool, mock_dir: Path, dataset_key: str, logger=print) -> "BrightDataClient":
        if mock:
            return cls(MockTransport(mock_dir, dataset_key), logger)
        return cls(LiveTransport(os.environ.get("BRIGHTDATA_API_KEY", "")), logger)

    def collect(
        self,
        dataset_id: str,
        inputs: list[dict],
        params: dict,
        fmt: str = "json",
        batch_size: int | None = None,
        poll_interval: float = 5.0,
        poll_timeout: float = 900.0,
    ) -> tuple[str, list[dict]]:
        """Full async cycle. Returns (snapshot_id, records)."""
        if not inputs:
            raise BrightDataError("inputs is empty, nothing to collect")
        self.log(f"[bd:{self.mode}] trigger dataset_id={dataset_id} inputs={len(inputs)}")
        snapshot_id = self.transport.trigger(dataset_id, inputs, params)
        self.log(f"[bd:{self.mode}] snapshot_id={snapshot_id}")
        self._wait_ready(snapshot_id, poll_interval, poll_timeout)
        records = self._download_all(snapshot_id, fmt, batch_size)
        self.log(f"[bd:{self.mode}] downloaded {len(records)} record(s)")
        return snapshot_id, records

    def _wait_ready(self, snapshot_id: str, interval: float, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            status = (self.transport.progress(snapshot_id) or {}).get("status", "unknown")
            self.log(f"[bd:{self.mode}] progress status={status}")
            if status == TERMINAL_OK:
                return
            if status == TERMINAL_FAIL:
                raise BrightDataError(f"snapshot {snapshot_id} failed on Bright Data side")
            if time.monotonic() > deadline:
                raise BrightDataError(f"snapshot {snapshot_id} not ready within {timeout:.0f}s")
            time.sleep(interval if self.mode == "live" else 0)

    def _download_all(self, snapshot_id: str, fmt: str, batch_size: int | None) -> list[dict]:
        """Walk every delivery part. Bright Data requires batch_size >= 1000."""
        if batch_size is None:
            return self.transport.download(snapshot_id, fmt, None, None)
        if batch_size < 1000:
            raise BrightDataError("batch_size must be >= 1000 (Bright Data download limit)")
        # The parts endpoint answers "Dataset is not built, call get snapshot to
        # trigger a build" until a plain snapshot GET has run once. For snapshots
        # that fit in a single delivery part that same call already returns
        # everything, so we keep it instead of downloading twice.
        first = self.transport.download(snapshot_id, fmt, None, None)
        try:
            total = self.transport.parts(snapshot_id, batch_size)
        except BrightDataError as exc:
            self.log(f"[bd:{self.mode}] parts unavailable ({exc}); using single-request download")
            return first
        if total <= 1:
            self.log(f"[bd:{self.mode}] single part: {len(first)} record(s)")
            return first
        records: list[dict] = []
        for part in range(1, total + 1):
            chunk = self.transport.download(snapshot_id, fmt, batch_size, part)
            self.log(f"[bd:{self.mode}] part {part}/{total}: {len(chunk)} record(s)")
            if not chunk:
                break
            records.extend(chunk)
        return records

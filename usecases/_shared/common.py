"""공용 유틸: HTTP 왕복, robots.txt 확인, 속도 제한, CSV/JSON 입출력, 실행 로그.

표준 라이브러리만 사용한다. 레포 루트의 무의존성 원칙을 그대로 따른다.
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib import robotparser

UA = "QJC-research/1.0 (+https://qjc.app)"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_last_call: dict[str, float] = {}
_robots_cache: dict[str, tuple[bool, str]] = {}


class Fetcher:
    """호스트별 최소 간격을 지키는 HTTP 클라이언트."""

    def __init__(self, min_interval: float = 1.2, ua: str = UA, log=print) -> None:
        self.min_interval = min_interval
        self.ua = ua
        self.log = log

    def _throttle(self, host: str) -> None:
        prev = _last_call.get(host, 0.0)
        wait = self.min_interval - (time.monotonic() - prev)
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()

    def get(self, url: str, *, timeout: int = 40, accept: str = "application/json",
            retries: int = 2) -> tuple[int, str]:
        host = urllib.parse.urlparse(url).netloc
        last = (0, "")
        for attempt in range(retries + 1):
            self._throttle(host)
            req = urllib.request.Request(
                url, headers={"User-Agent": self.ua, "Accept": accept}
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.status, resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:300]
                last = (exc.code, body)
                if exc.code in (429, 500, 502, 503) and attempt < retries:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                return last
            except Exception as exc:  # noqa: BLE001 - 네트워크 계열 전체를 로그로 흘린다
                last = (0, repr(exc)[:200])
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
        return last

    def get_json(self, url: str, **kw) -> tuple[int, object]:
        code, text = self.get(url, **kw)
        if code != 200:
            return code, None
        try:
            return code, json.loads(text)
        except json.JSONDecodeError:
            return code, None

    def post_json(self, url: str, payload: dict, *, timeout: int = 40) -> tuple[int, object]:
        host = urllib.parse.urlparse(url).netloc
        self._throttle(host)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"User-Agent": self.ua, "Content-Type": "application/json",
                     "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")[:300]
        except Exception as exc:  # noqa: BLE001
            return 0, repr(exc)[:200]


def robots_allows(url: str, ua: str = UA, log=print) -> tuple[bool, str]:
    """robots.txt를 실제로 읽어 접근 허용 여부를 판정한다.

    RFC 9309 기준으로 4xx(unavailable)는 제한 없음으로 해석되지만, 여기서는
    보수적으로 '확인 불가'를 별도 사유로 남겨 호출부가 판단하게 한다.
    """
    parts = urllib.parse.urlparse(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base in _robots_cache:
        return _robots_cache[base]
    fetch = Fetcher(min_interval=0.5, ua=ua, log=log)
    code, text = fetch.get(base + "/robots.txt", accept="text/plain", retries=1)
    if code != 200:
        result = (False, f"robots.txt 확인 불가 (HTTP {code})")
    else:
        rp = robotparser.RobotFileParser()
        rp.parse(text.splitlines())
        allowed = rp.can_fetch(ua, url) and rp.can_fetch("*", url)
        result = (allowed, "robots.txt 허용" if allowed else "robots.txt 차단")
    _robots_cache[base] = result
    log(f"[robots] {base} -> {result[1]}")
    return result


class RunLog:
    """표준 출력과 파일에 동시에 기록하는 실행 로그."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("a", encoding="utf-8")
        self.write(f"===== run start {now_iso()} =====")

    def write(self, msg: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    __call__ = write

    def close(self) -> None:
        self.write(f"===== run end {now_iso()} =====")
        self.fh.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d")


def write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _flat(r.get(k)) for k in fields})
    return path


def _flat(v):
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else v


def die(msg: str, code: int = 2):
    print(msg, file=sys.stderr)
    raise SystemExit(code)

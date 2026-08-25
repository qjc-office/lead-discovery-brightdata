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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from robots import PARSE_LIMIT_BYTES, RobotsRules, path_of  # noqa: E402

UA = "QJC-research/1.0 (+https://qjc.app)"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_last_call: dict[str, float] = {}

# 호스트별로 **파싱한 규칙**을 캐시한다. 판정 결과가 아니다.
# RFC 9309 §2.4 가 캐시를 허용한 대상은 robots.txt 의 내용이지 판정이 아니고,
# 판정을 캐시하면 같은 호스트의 다른 경로가 첫 경로의 답을 물려받아 조용히 뚫린다.
_robots_cache: dict[str, tuple[int, "RobotsRules | None"]] = {}


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
            retries: int = 2, max_bytes: int | None = None) -> tuple[int, str]:
        host = urllib.parse.urlparse(url).netloc
        last = (0, "")
        for attempt in range(retries + 1):
            self._throttle(host)
            req = urllib.request.Request(
                url, headers={"User-Agent": self.ua, "Accept": accept}
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read(max_bytes) if max_bytes else resp.read()
                    return resp.status, raw.decode("utf-8", "replace")
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


def robots_allows(url: str, ua: str = UA, log=print) -> tuple[bool, str, str]:
    """robots.txt를 실제로 읽어 접근 허용 여부를 판정한다.

    (허용여부, 사유, 상태) 세 개를 돌려준다. 상태는 RFC 9309 의 분류를 그대로 쓴다.

      allowed      robots.txt 를 읽었고 **이 URL 의 경로**가 허용
      disallowed   robots.txt 를 읽었고 이 URL 의 경로가 금지
      unavailable  4xx(429 제외)·리다이렉트 소진. §2.3.1.3·§2.3.1.2 는 크롤러가
                   접근해도 된다(MAY)고 본다
      unreachable  5xx, 429, 네트워크 실패. §2.3.1.4 는 전면 금지로 간주해야
                   한다(MUST)고 못 박는다

    **판정 대상은 넘긴 URL 그 자체다.** robots.txt 는 authority 단위로 존재하지만
    Allow/Disallow 는 경로 단위다(§2.2.2). 목록 페이지로 판정하고 API 로 요청하는
    식으로 대상이 어긋나면 게이트가 있으나 마나다. 실제로 요청할 URL 을 넘겨라.

    429(Too Many Requests)는 RFC 본문이 따로 규정하지 않는다. §2.3.1.3 이 4xx 를
    "예를 들어(for example)" 로만 들기 때문에 해석의 여지가 있는데, 여기서는
    unreachable 로 본다. 근거 둘.

      * 429 는 "파일이 없다"가 아니라 "지금은 그만 보내라"는 뜻이다. 그걸
        "규칙이 없으니 마음껏 긁어도 된다"로 읽으면 정확히 반대로 행동하게 된다.
      * 같은 파일 Fetcher.get() 이 이미 429 를 500·502·503 과 함께 재시도 대상으로
        묶고 있다. 재시도가 소진된 뒤 갑자기 403 과 같은 칸에 넣으면 한 코드베이스가
        429 를 두 가지로 다루는 셈이 된다.

    5개째 상태를 만들지 않고 unreachable 에 합친 것도 의도다. 상태를 일일이
    분기하지 않고 unreachable 만 보는 호출부가 자동으로 멈추는 쪽이 안전하다.

    허용여부는 두 경우(unavailable·unreachable) 모두 False 다. 표준이 갈리는
    지점이라 호출부가 상태를 보고 직접 판단하게 남겨 둔다. 상태를 안 보고
    False 만 보면 항상 멈추므로 그 자체로도 안전한 기본값이다.
    """
    parts = urllib.parse.urlparse(url)
    base = f"{parts.scheme}://{parts.netloc}"

    # 캐시에는 파싱한 규칙만 담는다. 판정은 매번 이 URL 의 경로로 새로 한다.
    if base in _robots_cache:
        code, rules = _robots_cache[base]
    else:
        fetch = Fetcher(min_interval=0.5, ua=ua, log=log)
        code, text = fetch.get(base + "/robots.txt", accept="text/plain", retries=1,
                               max_bytes=PARSE_LIMIT_BYTES)
        rules = RobotsRules(text, ua) if 200 <= code < 300 else None
        _robots_cache[base] = (code, rules)

    if rules is not None:
        # 2xx 는 성공 다운로드다(§2.3.1.1). 본문이 비어 규칙이 0개면 그대로 허용이 된다.
        allow, why = rules.decide(path_of(url))
        result = (allow, f"robots.txt {'허용' if allow else '차단'} ({why})",
                  "allowed" if allow else "disallowed")
    elif code == 429:
        # "그만 보내라"는 신호다. 4xx 범위이지만 unavailable 로 보면 정반대로 행동하게 된다.
        result = (False, "robots.txt 요청이 속도 제한에 걸림 (HTTP 429, 전면 금지로 간주)",
                  "unreachable")
    elif 300 <= code < 500:
        # 4xx 는 §2.3.1.3. 3xx 가 여기까지 왔다는 건 리다이렉트 상한을 넘겼다는 뜻이고
        # §2.3.1.2 도 그 경우를 unavailable 로 보라고 한다.
        result = (False, f"robots.txt 를 읽을 수 없음 (HTTP {code}, RFC 9309 unavailable)",
                  "unavailable")
    else:
        # 5xx·네트워크 실패(code 0). 표준이 전면 금지로 간주하라고 정한 구간이다.
        result = (False, f"robots.txt 서버 오류 (HTTP {code}, RFC 9309 unreachable, 전면 금지)",
                  "unreachable")
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

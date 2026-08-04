"""训记 Open API 客户端。纯标准库。

两个实测出来的坑，都硬编码在这里：

1. **urllib 不会自动解压。** 所有 `*_gzip` 端点返回 `Content-Encoding: gzip`，
   必须自己 `gzip.decompress()`。（curl --compressed 会自动解，所以手工调试时看不出来。）

2. **成功判据有两套。** 训练接口 `api_trains_for_llm_v2` 的响应里**没有**顶层
   `success` 字段，只有 `res`；而 body / food / plan 接口有 `success: true`。
   用一套判据会把训练接口的正常响应误判为失败。

限频器把「上次请求时间」写进磁盘，所以 cron 任务和交互式会话同时跑也不会互相撞限频。
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..config import DATA_DIR, get_key, redact

USER_AGENT = "selfcoach/0.1 (+https://github.com/yiding7/selfcoach)"

RATE_STATE = DATA_DIR / ".ratelimit.json"

# 服务端自报的限频（见 res.limits）。留一点余量，免得刚好卡在边界上被拒。
MARGIN_S = 1.0


class XunjiError(RuntimeError):
    """接口层面的错误。message 已经过 redact，可以安全展示给用户。"""

    def __init__(self, message: str, *, retry_after_ms: int | None = None,
                 kind: str = "error") -> None:
        super().__init__(redact(message))
        self.retry_after_ms = retry_after_ms
        self.kind = kind  # error | auth | vip | too_frequent | network


class MissingKey(XunjiError):
    def __init__(self, cred: str, env_var: str) -> None:
        super().__init__(
            f"缺少训记 {cred} 凭证（环境变量 {env_var}）。\n"
            f"  → 在训记 App 里申请 key，填进项目根目录的 .env\n"
            f"  → 或者跳过训记，直接用手记模式：hc log add",
            kind="auth",
        )


@dataclass(frozen=True)
class Endpoint:
    name: str          # 限频分组的 key
    url: str
    credential: str    # config.CREDENTIALS 里的名字
    min_interval_s: float
    gzip_response: bool
    envelope: str      # "res_only"（训练接口） | "success"（其余）


TRAIN_READ = Endpoint(
    "train_read", "https://trains.xunjiapp.cn/api_trains_for_llm_v2",
    "train", 30.0 + MARGIN_S, False, "res_only")
TRAIN_READ_LIGHT = Endpoint(
    "train_read", "https://trains.xunjiapp.cn/api_trains_for_llm_v2",
    "train", 15.0 + MARGIN_S, False, "res_only")
TRAIN_WRITE = Endpoint(
    "train_write", "https://trains.xunjiapp.cn/api_upsert_trains_for_llm_v2",
    "train", 45.0 + MARGIN_S, False, "res_only")
PLAN_QUERY = Endpoint(
    "plan_query", "https://api.xunjiapp.cn/open/plan/query_gzip",
    "train", 15.0 + MARGIN_S, True, "success")
BODY_QUERY = Endpoint(
    "body_query", "https://api.xunjiapp.cn/open/body/query_gzip",
    "body", 15.0 + MARGIN_S, True, "success")
BODY_UPSERT = Endpoint(
    "body_upsert", "https://api.xunjiapp.cn/open/body/upsert_gzip",
    "body", 15.0 + MARGIN_S, True, "success")
FOOD_QUERY = Endpoint(
    "food_query", "https://eatings.xunjiapp.cn/open/food/query_gzip",
    "food", 15.0 + MARGIN_S, True, "success")
FOOD_UPSERT = Endpoint(
    "food_upsert", "https://eatings.xunjiapp.cn/open/food/upsert_gzip",
    "food", 15.0 + MARGIN_S, True, "success")
FOOD_SEARCH = Endpoint(
    "food_search", "https://api.xunjiapp.cn/open_agent/food/search_gzip",
    "food_search", 15.0 + MARGIN_S, True, "success")


class RateLimiter:
    """跨进程的限频器。状态落盘，cron 和交互式会话共享。"""

    def __init__(self, path: Path = RATE_STATE) -> None:
        self.path = path

    def _load(self) -> dict[str, float]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, state: dict[str, float]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError:
            pass  # 限频状态丢了最多多等一次，不值得让整个同步失败

    def wait_for(self, endpoint: Endpoint, *, on_wait=None) -> None:
        state = self._load()
        last = state.get(endpoint.name, 0.0)
        elapsed = time.time() - last
        remaining = endpoint.min_interval_s - elapsed
        if remaining > 0:
            if on_wait:
                on_wait(remaining)
            time.sleep(remaining)

    def stamp(self, endpoint: Endpoint) -> None:
        state = self._load()
        state[endpoint.name] = time.time()
        self._save(state)


class XunjiClient:
    def __init__(self, *, limiter: RateLimiter | None = None, timeout: float = 45.0,
                 on_wait=None) -> None:
        self.limiter = limiter or RateLimiter()
        self.timeout = timeout
        self.on_wait = on_wait

    def has_key(self, credential: str) -> bool:
        return get_key(credential) is not None

    def post(self, endpoint: Endpoint, body: dict, *, max_retries: int = 3) -> dict:
        from ..config import CREDENTIALS

        key = get_key(endpoint.credential)
        if key is None:
            env_var, desc = CREDENTIALS[endpoint.credential]
            raise MissingKey(desc, env_var)

        payload = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if endpoint.gzip_response:
            headers["Accept-Encoding"] = "gzip"

        attempt = 0
        while True:
            attempt += 1
            self.limiter.wait_for(endpoint, on_wait=self.on_wait)
            req = urllib.request.Request(endpoint.url, data=payload,
                                         method="POST", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    if "gzip" in (resp.headers.get("Content-Encoding") or ""):
                        raw = gzip.decompress(raw)
                self.limiter.stamp(endpoint)
                data = json.loads(raw)
            except urllib.error.HTTPError as e:
                self.limiter.stamp(endpoint)
                detail = self._read_error(e)
                if e.code in (429, 500, 502, 503, 504) and attempt <= max_retries:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                raise XunjiError(f"HTTP {e.code}: {detail}") from None
            except urllib.error.URLError as e:
                if attempt <= max_retries:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                raise XunjiError(f"网络不可达: {e.reason}", kind="network") from None
            except json.JSONDecodeError:
                raise XunjiError("服务端返回的不是合法 JSON") from None

            err = self._check_error(data)
            if err is not None:
                if err.kind == "too_frequent" and attempt <= max_retries:
                    wait = (err.retry_after_ms or 16000) / 1000.0
                    if self.on_wait:
                        self.on_wait(wait)
                    time.sleep(wait)
                    continue
                raise err
            return data

    @staticmethod
    def _read_error(e: urllib.error.HTTPError) -> str:
        try:
            raw = e.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")[:300]
        except Exception:
            return e.reason or "unknown"

    @staticmethod
    def _check_error(data: dict) -> XunjiError | None:
        """把服务端的各种错误形态归一成异常。返回 None 表示成功。"""
        msg = ""
        for field in ("msg", "message", "error", "err"):
            v = data.get(field)
            if isinstance(v, str) and v:
                msg = v
                break

        low = msg.lower()
        if "too frequent" in low or "frequent" in low:
            retry = data.get("retry_after_ms") or data.get("retryAfterMs")
            return XunjiError(f"请求过于频繁: {msg}",
                              retry_after_ms=retry if isinstance(retry, int) else None,
                              kind="too_frequent")
        if "apikey" in low or "unauthorized" in low or "invalid" in low and "key" in low:
            return XunjiError(
                f"训记凭证无效或已过期（{msg}）。\n"
                f"  → 回训记 App 重新申请 key，更新 .env 里对应的变量",
                kind="auth")
        if "VIP" in msg or "会员" in msg:
            return XunjiError(f"该接口需要训记会员权限：{msg}", kind="vip")

        # success 为显式 False 才算失败；训练接口压根没有这个字段，不能当失败
        if data.get("success") is False:
            return XunjiError(msg or "接口返回 success=false")
        return None

    @staticmethod
    def unwrap(data: dict, endpoint: Endpoint) -> dict:
        """取出核心数据。两套信封各走各的路。"""
        res = data.get("res")
        if endpoint.envelope == "res_only":
            if not isinstance(res, dict):
                raise XunjiError("训练接口响应缺少 res 对象")
            return res
        if data.get("success") is not True:
            raise XunjiError("接口未返回 success=true")
        if not isinstance(res, dict):
            raise XunjiError("接口响应缺少 res 对象")
        return res

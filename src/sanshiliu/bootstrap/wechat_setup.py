"""Hermes 风格的 iLink 微信二维码登录；供首次启动向导补齐 wechat channel。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = str((2 << 16) | (2 << 8))
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"
DEFAULT_ACCOUNT_STORE = "data/wechat-account.json"
DEFAULT_QR_FILE = "data/wechat-login-qr.svg"
DEFAULT_QR_BOT_TYPE = "3"
DEFAULT_QR_TIMEOUT_SECONDS = 480
_MAX_QR_REFRESHES = 3
_QR_HTTP_TIMEOUT_SECONDS = 40.0


@dataclass(frozen=True)
class WechatCredentials:
    """iLink 官方 Bot 登录凭据；字段名兼容 Hermes 的 account store。"""

    account_id: str
    token: str
    base_url: str = ILINK_BASE_URL
    user_id: str = ""
    saved_at: str = ""

    def is_usable(self) -> bool:
        return bool(self.account_id.strip() and self.token.strip())

    def with_saved_at(self) -> WechatCredentials:
        return WechatCredentials(
            account_id=self.account_id,
            token=self.token,
            base_url=self.base_url or ILINK_BASE_URL,
            user_id=self.user_id,
            saved_at=datetime.now(UTC).isoformat(),
        )

    def to_json_dict(self) -> dict[str, str]:
        saved = self.with_saved_at()
        return {
            "account_id": saved.account_id,
            "token": saved.token,
            "base_url": saved.base_url,
            "user_id": saved.user_id,
            "saved_at": saved.saved_at,
        }

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> WechatCredentials:
        return cls(
            account_id=_text(value.get("account_id")),
            token=_text(value.get("token")),
            base_url=_text(value.get("base_url")) or ILINK_BASE_URL,
            user_id=_text(value.get("user_id")),
            saved_at=_text(value.get("saved_at")),
        )


@dataclass(frozen=True)
class QrLoginCode:
    qrcode: str
    scan_data: str


def wechat_channel_configured(values: dict[str, str]) -> bool:
    """判断当前 env 是否已有可用微信通道配置，不读取账号缓存。"""
    if _official_credentials_from_values(values) is not None:
        return True
    return bool(_text(values.get("ILINK_API_KEY")) and _text(values.get("ILINK_WEBHOOK_SECRET")))


async def run_wechat_channel_setup(
    values: dict[str, str],
    *,
    project_root: Path,
) -> dict[str, str]:
    """缺微信凭据时走 QR 登录；返回需要写入 .env 的更新键值。"""
    if _text(values.get("ILINK_API_KEY")) and _text(values.get("ILINK_WEBHOOK_SECRET")):
        print("  WeChat：已发现本地 iLink webhook 配置，跳过二维码登录。")
        return {}

    store_path = _resolve_path(
        values.get("WEIXIN_ACCOUNT_STORE") or DEFAULT_ACCOUNT_STORE,
        project_root=project_root,
    )
    saved_credentials = load_wechat_credentials(store_path)
    credentials = _merge_env_and_saved_credentials(values, saved_credentials)
    if credentials is not None:
        save_wechat_credentials(store_path, credentials)
        print(f"  WeChat：已加载已有凭据，账号缓存：{store_path}")
        return _env_updates_for_credentials(credentials, store_path)

    if not _env_bool(values.get("WEIXIN_QR_LOGIN"), default=True):
        print("  WeChat：WEIXIN_QR_LOGIN=false，跳过二维码登录。")
        return {}

    qr_file_path = _resolve_path(
        values.get("WEIXIN_QR_FILE") or DEFAULT_QR_FILE,
        project_root=project_root,
    )
    bot_type = _text(values.get("WEIXIN_QR_BOT_TYPE")) or DEFAULT_QR_BOT_TYPE
    timeout_seconds = _int_value(
        values.get("WEIXIN_QR_TIMEOUT_SECONDS"),
        default=DEFAULT_QR_TIMEOUT_SECONDS,
    )

    print("\n── WeChat 连接向导 ──")
    print("  未发现 wechat channel 凭据，将按 Hermes iLink Bot 流程生成二维码。")
    print("  请用微信扫描终端二维码，并在手机上确认登录。")
    print("  如终端二维码识别失败，可打开本地 SVG 备用文件。")

    try:
        credentials = await qr_login(
            qr_file_path=qr_file_path,
            bot_type=bot_type,
            timeout_seconds=timeout_seconds,
        )
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        print(f"  [WARN] WeChat 二维码登录未完成：{type(exc).__name__}: {exc}")
        _logger.warning("wechat QR 登录未完成", error=str(exc))
        return {}

    save_wechat_credentials(store_path, credentials)
    print(f"  [OK] WeChat 已连接；凭据已保存：{store_path}")
    return _env_updates_for_credentials(credentials, store_path)


def load_wechat_credentials(path: Path) -> WechatCredentials | None:
    """读取 Hermes 兼容的 wechat-account.json。"""
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("读取微信账号缓存失败", path=str(path), error=str(exc))
        return None
    if not isinstance(parsed, dict):
        return None
    credentials = WechatCredentials.from_mapping(parsed)
    return credentials if credentials.is_usable() else None


def save_wechat_credentials(path: Path, credentials: WechatCredentials) -> None:
    """原子覆盖保存账号缓存；扫码更新 token 时以新值覆盖旧值。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    body = json.dumps(credentials.to_json_dict(), ensure_ascii=False, indent=2)
    tmp_path.write_text(body + "\n", encoding="utf-8")
    tmp_path.replace(path)


async def qr_login(
    *,
    qr_file_path: Path,
    bot_type: str,
    timeout_seconds: int,
) -> WechatCredentials:
    """拉 QR、展示终端二维码、轮询确认状态，字段对齐 Hermes。"""
    timeout = httpx.Timeout(_QR_HTTP_TIMEOUT_SECONDS, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        current_base_url = ILINK_BASE_URL
        qr_code = await _fetch_qr_login_code(client, bot_type=bot_type)
        _present_qr_login_code(qr_code, qr_file_path)

        deadline = time.monotonic() + max(timeout_seconds, 1)
        refresh_count = 0
        last_status = ""
        while time.monotonic() < deadline:
            try:
                status_response = await _ilink_get(
                    client,
                    current_base_url,
                    EP_GET_QR_STATUS,
                    params={"qrcode": qr_code.qrcode},
                )
            except httpx.HTTPError as exc:
                _logger.warning("微信二维码状态轮询失败，将重试", error=str(exc))
                await asyncio.sleep(1.0)
                continue

            status = _string_field(status_response, ("status",)) or "wait"
            if status != last_status:
                _print_qr_status(status)
                last_status = status

            if status in {"wait", "scaned"}:
                pass
            elif status == "scaned_but_redirect":
                redirect_host = _string_field(status_response, ("redirect_host",))
                if redirect_host:
                    current_base_url = f"https://{redirect_host.rstrip('/')}"
            elif status == "expired":
                refresh_count += 1
                if refresh_count > _MAX_QR_REFRESHES:
                    raise RuntimeError("二维码过期次数过多")
                print("  WeChat：二维码已过期，正在刷新。")
                current_base_url = ILINK_BASE_URL
                qr_code = await _fetch_qr_login_code(client, bot_type=bot_type)
                _present_qr_login_code(qr_code, qr_file_path)
            elif status == "confirmed":
                return _credentials_from_confirmed_status(status_response)

            await asyncio.sleep(1.0)

    raise TimeoutError(f"WeChat 二维码登录超时（{timeout_seconds}s）")


async def _fetch_qr_login_code(client: httpx.AsyncClient, *, bot_type: str) -> QrLoginCode:
    response = await _ilink_get(
        client,
        ILINK_BASE_URL,
        EP_GET_BOT_QR,
        params={"bot_type": bot_type},
    )
    qrcode = _string_field(response, ("qrcode",))
    if not qrcode:
        raise ValueError("iLink QR 响应缺少 qrcode 字段")
    scan_data = _string_field(response, ("qrcode_img_content",)) or qrcode
    return QrLoginCode(qrcode=qrcode, scan_data=scan_data)


async def _ilink_get(
    client: httpx.AsyncClient,
    base_url: str,
    endpoint: str,
    *,
    params: dict[str, str],
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint}"
    response = await client.get(url, headers=_ilink_get_headers(), params=params)
    response.raise_for_status()
    parsed = response.json()
    if not isinstance(parsed, dict):
        raise ValueError(f"iLink endpoint {endpoint} 返回非对象 JSON")
    return parsed


def _present_qr_login_code(qr_code: QrLoginCode, path: Path) -> None:
    _write_qr_svg(qr_code.scan_data, path)
    _prefer_utf8_stdout()
    terminal_qr = _render_terminal_qr(qr_code.scan_data)
    print(f"\n  WeChat 登录二维码（备用文件：{path}）\n")
    print(terminal_qr)
    if not terminal_qr:
        print("  当前终端不适合显示可扫码二维码，请打开上面的 SVG 备用文件扫码。")
        if _should_open_qr_file():
            with contextlib.suppress(Exception):
                webbrowser.open(path.as_uri())
                print("  已尝试用默认浏览器打开二维码 SVG。")
    if qr_code.scan_data.startswith(("http://", "https://")):
        print(f"  备用扫码链接：{qr_code.scan_data}")


def _render_terminal_qr(data: str) -> str:
    _prefer_utf8_stdout()
    try:
        import qrcode
    except ImportError as exc:
        raise RuntimeError("缺少 qrcode 依赖，请运行 python -m pip install -e .") from exc

    qr = qrcode.QRCode(border=4)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    return _render_dense_1x2(matrix)


def _render_dense_1x2(matrix: list[list[bool]]) -> str:
    """按 Rust qrcode::render::unicode::Dense1x2 的思路用半块字符压缩两行。"""
    lines: list[str] = []
    for y in range(0, len(matrix), 2):
        top = matrix[y]
        bottom = matrix[y + 1] if y + 1 < len(matrix) else [False] * len(top)
        lines.append("".join(_dense_qr_cell(t, b) for t, b in zip(top, bottom, strict=True)))
    return "\n".join(lines)


def _dense_qr_cell(top: bool, bottom: bool) -> str:
    if top and bottom:
        return "█"
    if top:
        return "▀"
    if bottom:
        return "▄"
    return " "


def _prefer_utf8_stdout() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        with contextlib.suppress(Exception):
            reconfigure(encoding="utf-8", errors="replace")


def _should_open_qr_file() -> bool:
    if not sys.stdout.isatty():
        return False
    value = _text(os.environ.get("WEIXIN_QR_OPEN_FILE")).lower()
    return value not in {"0", "false", "no", "off"}


def _write_qr_svg(data: str, path: Path) -> None:
    try:
        import qrcode
        from qrcode.image.svg import SvgImage
    except ImportError as exc:
        raise RuntimeError("缺少 qrcode 依赖，请运行 python -m pip install -e .") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    qr = qrcode.QRCode(border=4, image_factory=SvgImage)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image()
    image.save(str(path))


def _credentials_from_confirmed_status(value: dict[str, Any]) -> WechatCredentials:
    account_id = _string_field(value, ("ilink_bot_id",))
    token = _string_field(value, ("bot_token",))
    if not account_id or not token:
        raise ValueError("iLink confirmed 响应缺少 ilink_bot_id 或 bot_token")
    return WechatCredentials(
        account_id=account_id,
        token=token,
        base_url=_string_field(value, ("baseurl",)) or ILINK_BASE_URL,
        user_id=_string_field(value, ("ilink_user_id",)) or "",
    )


def _official_credentials_from_values(values: dict[str, str]) -> WechatCredentials | None:
    account_id = _text(values.get("WEIXIN_ACCOUNT_ID"))
    token = _text(values.get("WEIXIN_TOKEN"))
    if not account_id or not token:
        return None
    return WechatCredentials(
        account_id=account_id,
        token=token,
        base_url=_text(values.get("WEIXIN_BASE_URL")) or ILINK_BASE_URL,
    )


def _merge_env_and_saved_credentials(
    values: dict[str, str],
    saved: WechatCredentials | None,
) -> WechatCredentials | None:
    env_account_id = _text(values.get("WEIXIN_ACCOUNT_ID"))
    env_token = _text(values.get("WEIXIN_TOKEN"))
    env_base_url = _text(values.get("WEIXIN_BASE_URL"))
    saved_matches = (
        saved if saved and (not env_account_id or env_account_id == saved.account_id) else None
    )

    account_id = env_account_id or (saved_matches.account_id if saved_matches else "")
    token = env_token or (saved_matches.token if saved_matches else "")
    if not account_id or not token:
        return None
    return WechatCredentials(
        account_id=account_id,
        token=token,
        base_url=env_base_url or (saved_matches.base_url if saved_matches else ILINK_BASE_URL),
        user_id=saved_matches.user_id if saved_matches else "",
        saved_at=saved_matches.saved_at if saved_matches else "",
    )


def _env_updates_for_credentials(
    credentials: WechatCredentials,
    store_path: Path,
) -> dict[str, str]:
    base_url = credentials.base_url.rstrip("/") or ILINK_BASE_URL
    return {
        "SANSHILIU_WECHAT_ENABLED": "true",
        "WEIXIN_ACCOUNT_ID": credentials.account_id,
        "WEIXIN_TOKEN": credentials.token,
        "WEIXIN_BASE_URL": base_url,
        "WEIXIN_ACCOUNT_STORE": str(store_path),
        "ILINK_BASE_URL": base_url,
        "ILINK_API_KEY": credentials.token,
    }


def _ilink_get_headers() -> dict[str, str]:
    return {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
        "User-Agent": "sanshiliu/1.0",
    }


def _print_qr_status(status: str) -> None:
    if status == "wait":
        print("  WeChat：等待扫码。")
    elif status == "scaned":
        print("  WeChat：已扫码，等待手机端确认。")
    elif status == "scaned_but_redirect":
        print("  WeChat：已扫码，正在切换服务地址。")
    elif status == "confirmed":
        print("  WeChat：手机端已确认。")
    else:
        print(f"  WeChat：二维码状态 {status}。")


def _string_field(value: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        candidate = value.get(key)
        text = _text(candidate)
        if text:
            return text
    for child in value.values():
        if isinstance(child, dict):
            text = _string_field(child, keys)
            if text:
                return text
    return ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _env_bool(value: str | None, *, default: bool) -> bool:
    text = _text(os.environ.get("WEIXIN_QR_LOGIN") or value).lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "y", "on"}


def _int_value(value: str | None, *, default: int) -> int:
    text = _text(value)
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _resolve_path(value: str, *, project_root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()

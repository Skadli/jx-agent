"""微信白名单；不在白名单内的 wxid 收到消息只记日志、不调 LLM、不回复。"""

from __future__ import annotations

from collections.abc import Iterable

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)


class WechatWhitelist:
    """简单 set 包装；空集合表示「未配置」，此时一律拒绝（最小特权）。"""

    def __init__(self, wxids: Iterable[str]) -> None:
        self._wxids = {w.strip() for w in wxids if w.strip()}

    @property
    def size(self) -> int:
        return len(self._wxids)

    def allows(self, wxid: str) -> bool:
        if not self._wxids:
            return False
        ok = wxid in self._wxids
        if not ok:
            _logger.info("wechat 非白名单消息忽略", wxid=wxid)
        return ok

    @classmethod
    def from_csv(cls, csv: str) -> WechatWhitelist:
        return cls(csv.split(",") if csv else [])

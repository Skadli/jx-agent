"""微信白名单；配置后限制 wxid，未配置时默认允许本地 bot 回复。"""

from __future__ import annotations

from collections.abc import Iterable

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)


class WechatWhitelist:
    """简单 set 包装；空集合表示未配置，此时允许所有 wxid。"""

    def __init__(self, wxids: Iterable[str]) -> None:
        self._wxids = {w.strip() for w in wxids if w.strip()}
        self._allow_all = "*" in self._wxids

    @property
    def size(self) -> int:
        return len(self._wxids)

    def allows(self, wxid: str) -> bool:
        if self._allow_all:
            return True
        if not self._wxids:
            return True
        ok = wxid in self._wxids
        if not ok:
            _logger.info("wechat 非白名单消息忽略", wxid=wxid)
        return ok

    @classmethod
    def from_csv(cls, csv: str) -> WechatWhitelist:
        return cls(csv.split(",") if csv else [])

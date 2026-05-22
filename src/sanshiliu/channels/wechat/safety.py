"""微信安全过滤；输入黑名单触发不回复，输出黑名单触发替换为提示话术。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sanshiliu.foundation.logging import get_logger

_logger = get_logger(__name__)

# 命中输出黑名单时统一替换文本；放在代码里因为它不是 LLM prompt，是给微信用户看的客服话术
_OUTPUT_REDACT_NOTICE = "[此回复因敏感词触发安全策略被过滤，请换个角度问问]"


@dataclass(frozen=True)
class SafetyDecision:
    blocked: bool
    redacted_text: str | None  # 仅 outbound 命中时给替换文本
    reason: str  # ok / input_blacklist / output_blacklist


class WechatSafety:
    """子串黑名单；空黑名单等同关闭对应方向。"""

    def __init__(
        self,
        *,
        input_blacklist: Iterable[str],
        output_blacklist: Iterable[str],
    ) -> None:
        self._in = [k.strip() for k in input_blacklist if k.strip()]
        self._out = [k.strip() for k in output_blacklist if k.strip()]

    def check_input(self, text: str) -> SafetyDecision:
        if not self._in:
            return SafetyDecision(blocked=False, redacted_text=None, reason="ok")
        for kw in self._in:
            if kw in text:
                _logger.warning("input 命中黑名单", keyword=kw, text=text[:40])
                return SafetyDecision(
                    blocked=True, redacted_text=None, reason="input_blacklist",
                )
        return SafetyDecision(blocked=False, redacted_text=None, reason="ok")

    def check_output(self, text: str) -> SafetyDecision:
        if not self._out:
            return SafetyDecision(blocked=False, redacted_text=None, reason="ok")
        for kw in self._out:
            if kw in text:
                _logger.warning("output 命中黑名单", keyword=kw, text=text[:40])
                return SafetyDecision(
                    blocked=True, redacted_text=_OUTPUT_REDACT_NOTICE,
                    reason="output_blacklist",
                )
        return SafetyDecision(blocked=False, redacted_text=None, reason="ok")

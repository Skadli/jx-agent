"""wechat 白名单 + 安全过滤单测（V-4 + V-9）。"""

from __future__ import annotations

from sanshiliu.channels.wechat.safety import WechatSafety
from sanshiliu.channels.wechat.whitelist import WechatWhitelist


def test_empty_whitelist_blocks_all() -> None:
    wl = WechatWhitelist([])
    assert wl.allows("anyone") is False


def test_whitelist_allows_listed_only() -> None:
    """V-4：非白名单消息不回复。"""
    wl = WechatWhitelist(["w1", "w2"])
    assert wl.allows("w1") is True
    assert wl.allows("w2") is True
    assert wl.allows("w3") is False


def test_whitelist_from_csv_strips_spaces() -> None:
    wl = WechatWhitelist.from_csv("w1, w2 , ,w3")
    assert wl.size == 3
    assert wl.allows("w1")
    assert wl.allows("w2")
    assert wl.allows("w3")


def test_safety_input_blacklist_blocks() -> None:
    s = WechatSafety(input_blacklist=["禁词"], output_blacklist=[])
    d = s.check_input("含禁词的消息")
    assert d.blocked is True
    assert d.reason == "input_blacklist"


def test_safety_input_clean() -> None:
    s = WechatSafety(input_blacklist=["禁词"], output_blacklist=[])
    d = s.check_input("普通消息")
    assert d.blocked is False


def test_safety_output_blacklist_replaces() -> None:
    s = WechatSafety(input_blacklist=[], output_blacklist=["脏话"])
    d = s.check_output("回复里有脏话")
    assert d.blocked is True
    assert d.redacted_text is not None
    assert "敏感词" in d.redacted_text


def test_safety_empty_lists_pass_through() -> None:
    s = WechatSafety(input_blacklist=[], output_blacklist=[])
    assert s.check_input("任何内容").blocked is False
    assert s.check_output("任何内容").blocked is False

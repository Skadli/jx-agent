"""TokenBudget 单测。"""

from __future__ import annotations

from sanshiliu.context.budget import TokenBudget


def test_threshold_uses_ratio() -> None:
    b = TokenBudget(max_tokens=10_000, compact_threshold_ratio=0.7)
    assert b.threshold == 7_000


def test_should_compact_only_after_threshold() -> None:
    b = TokenBudget(max_tokens=1_000, compact_threshold_ratio=0.8)
    b.update_from_usage(input_tokens=799, output_tokens=10)
    assert b.should_compact() is False
    b.update_from_usage(input_tokens=900, output_tokens=10)
    assert b.should_compact() is True


def test_update_accumulates() -> None:
    b = TokenBudget(max_tokens=1_000)
    b.update_from_usage(input_tokens=100, output_tokens=50, cache_read=10, cache_create=5)
    b.update_from_usage(input_tokens=200, output_tokens=80, cache_read=20, cache_create=15)
    s = b.stats()
    assert s["cumulative_input"] == 300
    assert s["cumulative_output"] == 130
    assert s["cache_read"] == 30
    assert s["cache_create"] == 20
    assert s["last_prompt_tokens"] == 200


def test_note_compact_resets_window() -> None:
    b = TokenBudget(max_tokens=1_000)
    b.update_from_usage(input_tokens=900, output_tokens=10)
    assert b.should_compact()
    b.note_compact()
    assert b.last_prompt_tokens == 0
    assert b.should_compact() is False
    assert b.compact_count == 1


def test_note_microcompact_counter() -> None:
    b = TokenBudget(max_tokens=1_000)
    b.note_microcompact()
    b.note_microcompact()
    assert b.microcompact_count == 2


def test_utilization_zero_when_max_zero() -> None:
    b = TokenBudget(max_tokens=0)
    assert b.utilization == 0.0


def test_stats_keys_stable() -> None:
    """REPL /stats 依赖这些 key；锁住契约。"""
    expected_keys = {
        "max_tokens",
        "threshold",
        "last_prompt_tokens",
        "utilization",
        "cumulative_input",
        "cumulative_output",
        "cache_read",
        "cache_create",
        "compact_count",
        "microcompact_count",
    }
    assert set(TokenBudget(max_tokens=1).stats().keys()) == expected_keys

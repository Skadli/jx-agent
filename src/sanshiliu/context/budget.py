"""Token budget；按最近一次 prompt_tokens 估算当前窗口大小，过阈值就触发 compact。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenBudget:
    """单会话 token 预算与累计。

    设计要点：
    - ``last_prompt_tokens`` = 上一次请求的 prompt_tokens（含 system+history），近似当前窗口占用
    - 阈值判定不看累计；看的是 last_prompt_tokens 是否已逼近 max_tokens * ratio
    - cache_read/cache_create 字段预留——多数 OpenAI 兼容后端无此返回，DeepSeek/Anthropic 才有
    """

    max_tokens: int
    compact_threshold_ratio: float = 0.8

    # 累计：仅审计、不参与 compact 判定
    cumulative_input: int = 0
    cumulative_output: int = 0
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0

    # 当前窗口大小估算：每次 LLM 返回 usage 后刷新
    last_prompt_tokens: int = 0

    # compact 触发次数；REPL /stats 用
    compact_count: int = 0
    microcompact_count: int = 0
    # C2：连续 compact 失败计数；达阈值开"熔断"暂停 compact，避免每轮反复失败烧 token
    consecutive_compact_failures: int = 0

    # 事件标记：本轮是否刚触发 compact（用于 engine 决定要不要等下一轮）
    _last_check_triggered: bool = field(default=False, repr=False)

    @property
    def threshold(self) -> int:
        return int(self.max_tokens * self.compact_threshold_ratio)

    @property
    def utilization(self) -> float:
        """0~1+；> 1 表示已经超 max_tokens（应该不会发生，LLM 自己会拒）。"""
        if self.max_tokens <= 0:
            return 0.0
        return self.last_prompt_tokens / self.max_tokens

    def update_from_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_create: int = 0,
    ) -> None:
        """每次 LLM 调用结束后调用；刷新累计和窗口估算。"""
        self.cumulative_input += input_tokens
        self.cumulative_output += output_tokens
        self.cache_read_tokens += cache_read
        self.cache_create_tokens += cache_create
        # C1：窗口估算计入 output——下一轮 prompt 会把本轮 output 当历史带上，只算 input 会迟触发
        # compact（对齐 CC tokenCountWithEstimation 同样计 output/cache）。多数 OpenAI 后端返
        # completion_tokens；不返时 output=0、退化回旧的"仅 input"行为。
        self.last_prompt_tokens = input_tokens + output_tokens

    def should_compact(self) -> bool:
        """阈值判定；触发后由调用方负责 compact，不在此处做副作用。"""
        return self.last_prompt_tokens >= self.threshold

    def note_compact(self) -> None:
        self.compact_count += 1
        self.consecutive_compact_failures = 0  # C2：成功即清零失败计数
        # compact 完成后窗口估算应大幅下降；让 last_prompt_tokens 临时归零，等下一次 LLM 返回 usage 后再补
        self.last_prompt_tokens = 0

    def note_compact_failure(self) -> None:
        """C2：compact（含一次重试）整体失败时 +1；达阈值后 compact_circuit_open 为真。"""
        self.consecutive_compact_failures += 1

    @property
    def compact_circuit_open(self) -> bool:
        """连续失败 ≥3 次 → 熔断：暂停 compact，避免每轮反复失败白烧 token
        （对齐 CC MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES=3）。一次成功即清零。"""
        return self.consecutive_compact_failures >= 3

    def note_microcompact(self) -> None:
        self.microcompact_count += 1

    def stats(self) -> dict[str, int | float]:
        """供 REPL /stats 与日志使用；字段名稳定。"""
        return {
            "max_tokens": self.max_tokens,
            "threshold": self.threshold,
            "last_prompt_tokens": self.last_prompt_tokens,
            "utilization": round(self.utilization, 3),
            "cumulative_input": self.cumulative_input,
            "cumulative_output": self.cumulative_output,
            "cache_read": self.cache_read_tokens,
            "cache_create": self.cache_create_tokens,
            "compact_count": self.compact_count,
            "microcompact_count": self.microcompact_count,
        }

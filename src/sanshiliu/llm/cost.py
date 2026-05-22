"""Token 成本估算；按 1K token CNY 计价，未知模型用保守默认价。"""

from __future__ import annotations

# 每 1K token 的 CNY 价格；官网 2025 公开价，汇率或降价需手动更新
_PRICE_TABLE: dict[str, tuple[float, float]] = {
    # model_name: (input_cny_per_1k, output_cny_per_1k)
    "gpt-4o":        (0.025, 0.1),
    "gpt-4o-mini":   (0.0015, 0.006),
    "gpt-4-turbo":   (0.07, 0.21),
    "deepseek-chat": (0.001, 0.002),
    "deepseek-reasoner": (0.004, 0.016),
    "glm-4-plus":    (0.05, 0.05),
    "qwen-plus":     (0.004, 0.012),
}
_DEFAULT_PRICE = (0.01, 0.03)  # 未知模型保守默认


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """估算单次调用 CNY 成本，按精确匹配、前缀匹配、默认价降级。"""
    if model in _PRICE_TABLE:
        in_price, out_price = _PRICE_TABLE[model]
    else:
        # 前缀匹配：gpt-4o-2024-08-06 → gpt-4o
        in_price, out_price = _DEFAULT_PRICE
        for known, prices in _PRICE_TABLE.items():
            if model.startswith(known):
                in_price, out_price = prices
                break

    return round((input_tokens * in_price + output_tokens * out_price) / 1000, 6)

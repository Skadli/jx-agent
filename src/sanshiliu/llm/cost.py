"""Token 成本计算（CNY）。

简化版价格表：按 1K token 折算 CNY；未知模型按保守默认价。
Phase 1 准确度要求不高，能区分模型成本量级即可；后续可接 model_price.yaml 热更新。
"""

from __future__ import annotations

# 价格表：每 1K token 价格（CNY）
# 数据来源：各家官网 2025 年公开价；切换汇率/降价时手动维护
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
    """估算单次调用 CNY 成本。

    匹配规则：完全相等 > 前缀（去掉版本号尾巴）> 默认价。
    """
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

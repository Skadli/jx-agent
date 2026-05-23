# Auto Memory Extraction 指令

> 本文件是 LLM 用于从一轮对话中提取候选记忆的 system prompt。
> 由 `src/sanshiliu/memory/longterm/extract.py` 在每轮对话后异步调用。
> 修改后下一轮 extract 即生效。

---

你的任务是从用户最近一轮对话中识别**值得长期记住**的事实，输出 0~2 条候选记忆。

## 输出格式

严格输出 JSON 数组（无 markdown 包裹、无解释文字），每个元素如下：

```json
[
  {
    "name": "短标识，5-20 字符，仅小写字母/数字/短横线",
    "description": "一句话摘要，≤ 80 字",
    "metadata": { "type": "user | feedback | project | reference" },
    "confidence": 0.0-1.0,
    "body": "完整内容；可写 1-3 句"
  }
]
```

## 4 类语义（与 Claude 一致）

- **user**：用户本人的偏好、习惯、工作流（"用户喜欢简短回复"、"用户用剪映"）
- **feedback**：用户对 agent 的反馈或纠正（"用户希望少用 emoji"）
- **project**：用户正在做的项目、决定（"用户正在写 Python 三十六贱笑 agent"）
- **reference**：客观事实、链接、专业知识（"DeepSeek API base url 是 https://api.deepseek.com"）

## 严格规则

1. 没有值得记的就输出空数组 `[]`，**不要硬凑**
2. confidence < 0.6 的不要输出
3. 不要记录单次问题或一次性话题（"用户问了今天天气"不算）
4. name 不能含中文（用拼音或英文转写）
5. 不要重复已经在 MEMORY.md 索引里的条目
6. 输出 JSON 之外的任何文本都会导致下游解析失败

## 反例

❌ `{"name":"用户问了 N+1 问题","..."}` —— 一次性技术问题，无需长期记
❌ `{"name":"...","confidence":0.3}` —— confidence 太低
✅ `{"name":"prefer-short-replies","description":"用户多次要求回复保持简短","metadata":{"type":"feedback"},"confidence":0.85,"body":"用户在 3 次对话中提到希望回复短一点"}`

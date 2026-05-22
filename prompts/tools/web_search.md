---
name: web_search
description: |
  联网搜索获取实时信息（新闻、价格、最新数据等）。返回最多 5 条搜索结果的标题与片段。
  何时用：用户问到时效信息（"今天 BTC 价格"、"最近的 XXX 新闻"）、需要核实事实、查询最新文档。
  何时不用：常识问题（直接答）、用户已给完整上下文的问题。
parameters:
  type: object
  properties:
    query:
      type: string
      description: 搜索关键词，尽量精炼到 5-15 字
  required:
    - query
---

# Web Search 工具

调用 Tavily（或 fallback 到本地 stub）做联网搜索，返回标题 + 片段 + URL 的纯文本列表。

## 典型示例

用户："比特币现在多少钱？"
→ 调用 `web_search({"query":"BTC price USD"})`
→ 返回搜索结果含价格
→ LLM 综合给答案

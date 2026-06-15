---
name: web_search
description: |
  联网搜索：返回最多 5 条搜索结果（标题 + 内容片段 + URL）。优先用 Tavily，缺 key 时走 DuckDuckGo。
  必须用本工具的情形（不要拿 bash_exec / file_read 凑，它们没有联网能力）：
    - 时效信息：今天的天气 / 股价 / 比特币价格 / 汇率 / 最新新闻 / 体育比分 / 演唱会票务
    - 你不知道或不确定的事实（人物、事件、产品规格、政策、价格、时间表）
    - 需要核实你内存里的事实（你的知识有截止日期，时效问题一律去搜索）
  不该用：
    - 用户给了完整上下文的问题
    - 不需要联网就能答的常识、定义、解释类
    - 用户明确说"别搜索"
    - system prompt「可用技能」里已有某 skill 的 description 覆盖了这个场景：先调 Skill 拿正文，别拿搜索凑
  优先级：当问题既可能涉及实时信息又可能不涉及时，先搜索更稳；但本地 skill 能覆盖的场景，Skill 优先于搜索。
parameters:
  type: object
  properties:
    query:
      type: string
      description: 搜索关键词。控制在 5-20 字，去口语化保留核心实体；中英文都行。
  required:
    - query
---

# Web Search 工具

调用 Tavily（或 fallback 到 DuckDuckGo HTML）做联网搜索，返回标题 + 片段 + URL 的纯文本列表。

## 典型示例

用户："比特币现在多少钱？"
→ 调用 `web_search({"query":"BTC 价格"})`
→ 返回搜索结果含价格
→ 综合给答案

用户："深圳今天天气怎么样？"
→ 调用 `web_search({"query":"深圳今天天气"})`

用户："最新的 Claude 4 发布会讲了啥？"
→ 调用 `web_search({"query":"Claude 4 发布会"})`

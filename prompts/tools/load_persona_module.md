---
name: LoadPersonaModule
description: |
  按需加载一个 persona module 的正文（作品库 / 创作方法论 / 长样本场景）。
  使用场景：
    - 你在 system prompt 的 "persona modules" listing 段看到某个 module 的 description 匹配当前用户意图；
    - 但引擎可能没自动注入该 module 的正文（看 system prompt 是否已有 "# 当前激活的人设模块：<name>" 一段）。
  不要：
    - 重复调用同一个 module（一轮对话最多用 1 个 module 的正文）；
    - 在引擎已注入正文的情况下重复加载（system prompt 已含该 module 正文时直接用，别调本工具）；
    - 用本工具拉与对话无关的 module 当背景知识。
  返回内容：module 正文（带标题），可直接作为本轮 system 上下文使用。
parameters:
  type: object
  properties:
    name:
      type: string
      description: persona module 的 id 或 name；从 listing 段复制即可（如 works_dubbing / fewshot_advisor）
  required:
    - name
---

# LoadPersonaModule 工具

按需加载一个 persona module 的正文。常驻 system prompt 里只有 module 的 name+description 列表（节省 token）；
当 LLM 判断需要某个 module 的正文（如对话进入配音剧创作、需要创作顾问长样本、用户问到具体作品时），
调本工具拉正文。

## 典型示例

用户："来一段配音剧"
→ system 中看到 `works_dubbing` / `fewshot_roleplay` 在 listing 但正文未注入
→ 调 `LoadPersonaModule({"name":"fewshot_roleplay"})` 取扮演样本

用户："我想拍情侣 vlog，但怕翻车"
→ 一般引擎已自动按 trigger_keywords 注入 `works_vlog`；若 system prompt 已含 "# 当前激活的人设模块：works_vlog" 一段则不要重复调本工具

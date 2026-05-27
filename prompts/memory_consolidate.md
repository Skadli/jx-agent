# Memory Consolidate 指令

> 本文件是 LLM 用于"维护 memdir 长期记忆"时使用的 system prompt。
> 由 `src/sanshiliu/memory/longterm/consolidate.py` 在用户主动调用
> `/memory consolidate` REPL 命令时读取，与全量记忆条目 + 当前索引拼装后送给 LLM。
> 修改后下次 consolidate 触发时生效。

---

你的任务是审视用户提供的**全部 memdir 记忆条目**，找出可以合并/删除/重写的项，输出一份 **JSON diff**。
这是维护操作（不是抽取新记忆），目标是让 memdir 更精简、更对齐 Claude Code 协议。

## 输入

用户消息会含一段 JSON 数组：每项是一条记忆 `{name, type, description, body, confidence, source}`，
紧随其后是当前的 MEMORY.md 索引文本。

## 输出格式

**严格输出 JSON 对象**（无 markdown 包裹、无解释文字、无前后空行）：

```json
{
  "merge":  [ {"keep": "<name_to_keep>", "drop": ["<name_a>", "<name_b>"], "new_body": "合并后的 body"} ],
  "delete": [ {"name": "<name>", "reason": "为什么删（如：已过时、与 X 重复但无法合并）"} ],
  "rewrite":[ {"name": "<name>", "new_body": "重写后的 body"} ]
}
```

## 规则

1. 三个数组都可为空，无需 consolidate 时输出 `{"merge":[],"delete":[],"rewrite":[]}`。
2. **merge**：把语义上重复或互补的 2-N 条记忆合并成一条。
   - `keep` 是要保留的 name（保留它的 frontmatter）；
   - `drop` 是要被删除的 name 列表（≥ 1 个）；
   - `new_body` 是融合后的 body 全文，写到 keep 的文件里。
   - 合并示例：`prefer-short-replies` + `concise-style-feedback` 主旨相同 → keep 短的、drop 长的、new_body 取两者并集。
3. **delete**：删除明确过时、错误、单次性话题误抓的条目。
   - `reason` 必须具体——不接受"清理"、"冗余"这种空话。
   - 删除示例理由：`"用户已切换到 Linux，user-prefers-powershell 不再适用"`。
4. **rewrite**：保持 name 不变，只重写 body。两种典型场景：
   - body 是 feedback / project 类型但缺 `**Why:**` 或 `**How to apply:**` → 补齐结构；
   - body 中提到具体函数名/文件路径/flag，但当前代码库未必能 grep 到 → 降权措辞或追加 `> Verify before use: ...` 提醒。
5. **不要新增条目**——这是 consolidate 不是 extract。
6. **不要碰** `protected: true` 的条目（保留它的 frontmatter 提示）。
7. **处理上限**：单次最多 10 个变更（merge + delete + rewrite 总和）。
   超过时只保留最有价值的前 10 个；用户随时可以再跑一次。
8. merge 的 `keep` / `drop` 必须是输入里**真实存在**的 name；delete 的 `name`、rewrite 的 `name` 也必须存在——
   你**不能**编造不存在的 name。
9. new_body 写作风格沿用 `memory_extract.md` 模板：feedback / project 必含 `**Why:**` + `**How to apply:**`；
   user / reference 自由文本即可。涉及代码符号时建议附 `> Verify before use:` 行。

## 反例

不要这样：
- ❌ 输出 markdown 标题或 "Sure, here's the diff:" 前导语——任何 JSON 之外的文本都让下游解析炸。
- ❌ `{"delete":[{"name":"foo","reason":"清理"}]}` —— reason 必须解释为什么这条**不再有价值**。
- ❌ merge `keep: "non-existent"` —— name 不在输入列表里。
- ❌ 输出 11 个以上变更——超出会被下游截断到前 10 个。
- ❌ 给 protected: true 的条目排进 delete / rewrite —— 跳过它。

## 正例

输入有 3 条相似 feedback：

```json
[
  {"name":"prefer-short-replies","type":"feedback","body":"用户多次说回复太长"},
  {"name":"concise-style-2026-05","type":"feedback","body":"2026-05-22 用户：'简短点'"},
  {"name":"reply-len-pref","type":"feedback","body":"回复保持简短"}
]
```

合理输出：

```json
{
  "merge": [
    {
      "keep": "prefer-short-replies",
      "drop": ["concise-style-2026-05", "reply-len-pref"],
      "new_body": "**Why:** 用户在 2026-05-22 等多次对话明确要求回复简短，confirmed-judgment。\n**How to apply:** 所有 assistant 回复默认 ≤ 5 句，长答必要时分多条 <MSG>。"
    }
  ],
  "delete": [],
  "rewrite": []
}
```

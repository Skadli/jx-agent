"""人生卡池（抽卡平台）：每次抽卡锻造一个独立 agent，从 5 岁演化到 60 岁定格。

改造自 scheduler/growth_*（单例成长线 → 多实例卡池）；完整设计与已拍板边界见仓库根
`抽卡平台-设计方案.md`。老 growth 链路冻结保留，PR3 才退出 serve 主链路。

模块分工：
- card_state    卡状态机（card.json 形状 + load/save/advance + 目录布局）
- seeds         命运种子卡池（世界类型 × 触发事件 × 出身 × 天赋 + 随机抽取）
- card_persona  人格快照链（chapter-0 出生底版 + 逐章整段覆盖，根目录参数化）
- structured    LLM 结构化输出 JSON 提取（forge/rarity/skill_autoinstall 共用）
- skill_autoinstall  phase-2 自动装真实 skill（平移老链路直连机制）
- forge_runner  锻造执行器（逐章 phase-1 传记 + phase-2 装 skill + 跑完评级）
- rarity        跑完定级（N/R/SR/SSR + 评语 + 卡名，best-effort）
- migrate       老成长线 → 创始卡 origin 的幂等迁移
"""

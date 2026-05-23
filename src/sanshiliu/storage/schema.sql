-- sqlite schema v1.0；Phase 1 数据结构，启动时幂等执行

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- LLM 调用记账；每次 chat.completions 一行，含 base_url
CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY,
    ts            INTEGER NOT NULL,           -- unix ms
    session_id    TEXT    NOT NULL,
    channel       TEXT    NOT NULL,           -- repl / wechat / web
    user_id       TEXT,
    model         TEXT    NOT NULL,
    base_url      TEXT    NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_cny      REAL    NOT NULL DEFAULT 0,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    stop_reason   TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls(ts);
CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id);

-- 会话表
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    channel         TEXT    NOT NULL,
    user_id         TEXT,
    created_at      INTEGER NOT NULL,
    last_active_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_channel_user ON sessions(channel, user_id);

-- 通道消息表，Phase 4 使用
CREATE TABLE IF NOT EXISTS channel_messages (
    id           INTEGER PRIMARY KEY,
    ts           INTEGER NOT NULL,
    channel      TEXT    NOT NULL,
    direction    TEXT    NOT NULL,           -- in / out
    session_id   TEXT    NOT NULL,
    user_id      TEXT,
    group_id     TEXT,
    content      TEXT    NOT NULL,
    msg_type     TEXT    NOT NULL,           -- text / image / ...
    processed    INTEGER NOT NULL DEFAULT 0,
    llm_call_id  INTEGER REFERENCES llm_calls(id)
);
CREATE INDEX IF NOT EXISTS idx_channel_messages_session ON channel_messages(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_channel_messages_unproc ON channel_messages(processed, ts);

-- 官方 iLink Bot 长轮询状态；避免重启后从空 cursor 重放历史消息
CREATE TABLE IF NOT EXISTS wechat_ilink_state (
    account_id   TEXT PRIMARY KEY,
    sync_buf     TEXT    NOT NULL DEFAULT '',
    updated_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS wechat_ilink_seen (
    dedup_key    TEXT PRIMARY KEY,
    ts           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wechat_ilink_seen_ts ON wechat_ilink_seen(ts);

-- 限流计数器，Phase 4 用
CREATE TABLE IF NOT EXISTS rate_limit_counters (
    scope         TEXT    NOT NULL,           -- user:<wxid> / global / channel:wechat
    window_start  INTEGER NOT NULL,           -- 时间窗起点 unix sec
    count         INTEGER NOT NULL,
    PRIMARY KEY (scope, window_start)
);

-- 权限决策，Phase 8 用
CREATE TABLE IF NOT EXISTS permission_decisions (
    id           INTEGER PRIMARY KEY,
    ts           INTEGER NOT NULL,
    session_id   TEXT    NOT NULL,
    tool_name    TEXT    NOT NULL,
    decision     TEXT    NOT NULL,            -- allow / deny / always
    scope        TEXT    NOT NULL,            -- once / session / permanent
    pattern      TEXT                          -- e.g. Bash(npm test:*)
);
CREATE INDEX IF NOT EXISTS idx_perm_session ON permission_decisions(session_id, ts);

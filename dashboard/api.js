/* dashboard ↔ agent fetch 层。
 * 全部走同源 /api/*；window.API.get/post/put/del 返回 Promise<json>。
 * 失败时不抛，返回 {error: "..."}；调用方据此显示错误条。
 */
(function () {
  const base = ""; // 同源
  const TOKEN_KEY = "jx_dashboard_token";

  function token() {
    try { return localStorage.getItem(TOKEN_KEY) || ""; }
    catch (e) { return ""; }
  }

  function setToken(value) {
    try {
      if (value) localStorage.setItem(TOKEN_KEY, value);
      else localStorage.removeItem(TOKEN_KEY);
    } catch (e) {}
  }

  async function _json(resp) {
    const text = await resp.text();
    if (!text) return { ok: resp.ok };
    try {
      const data = JSON.parse(text);
      if (!resp.ok && !data.error) data.error = `HTTP ${resp.status}`;
      return data;
    } catch (e) {
      return { error: `bad JSON: ${text.slice(0, 200)}`, status: resp.status };
    }
  }

  async function _req(method, path, body) {
    try {
      const headers = { "Content-Type": "application/json" };
      const t = token();
      if (t) headers["X-Dashboard-Token"] = t;
      const opts = { method, headers };
      if (body !== undefined) opts.body = JSON.stringify(body);
      const resp = await fetch(base + path, opts);
      return await _json(resp);
    } catch (e) {
      return { error: String(e) };
    }
  }

  // 带鉴权拉二进制 → object URL（历史图片懒加载用；裸 <img> 不带 token 会 401）。
  // 失败回 ""；调用方负责在不用时 URL.revokeObjectURL 释放。
  async function blobUrl(path) {
    try {
      const headers = {};
      const t = token();
      if (t) headers["X-Dashboard-Token"] = t;
      const resp = await fetch(base + path, { headers });
      if (!resp.ok) return "";
      const blob = await resp.blob();
      return URL.createObjectURL(blob);
    } catch (e) {
      return "";
    }
  }

  // 用 fetch + ReadableStream 解 SSE；data: ... 行触发 onDelta；event: done/error/approval/msg_break 触发对应 cb
  // 返回 {abort: ()=>void}
  function chatStream({ q, sessionId, images, onSession, onApproval, onDelta, onMsgBreak, onDone, onError }) {
    const ctrl = new AbortController();
    const body = {
      q,
      ...(images && images.length ? { images } : {}),
      ...(sessionId ? { session_id: sessionId } : {}),
    };

    (async () => {
      try {
        const headers = { "Content-Type": "application/json" };
        const t = token();
        if (t) headers["X-Dashboard-Token"] = t;
        const resp = await fetch("/chat", {
          method: "POST",
          headers,
          body: JSON.stringify(body),
          signal: ctrl.signal,
        });
        if (!resp.ok || !resp.body) {
          onError && onError(`HTTP ${resp.status}`);
          return;
        }
        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";
        let currentEvent = "message";
        let dataLines = [];
        let finished = false;

        const flush = () => {
          // msg_break：<MSG> 段边界，无有效 data；不能当成 delta（否则裸标签/不分气泡）
          if (currentEvent === "msg_break") {
            dataLines = [];
            currentEvent = "message";
            onMsgBreak && onMsgBreak();
            return;
          }
          if (dataLines.length === 0) return;
          const data = dataLines.join("\n");
          dataLines = [];
          if (currentEvent === "done") {
            onDone && onDone(data);
            finished = true;
          } else if (currentEvent === "error") {
            onError && onError(data);
            finished = true;
          } else if (currentEvent === "session") {
            onSession && onSession(data);
          } else if (currentEvent === "approval") {
            try {
              onApproval && onApproval(JSON.parse(data));
            } catch (e) {
              onError && onError(`bad approval payload: ${String(e)}`);
            }
          } else {
            onDelta && onDelta(data);
          }
          currentEvent = "message";
        };

        while (true) {
          const { value, done } = await reader.read();
          if (done) { flush(); break; }
          buffer += decoder.decode(value, { stream: true });
          // SSE 帧以空行分隔
          let idx;
          while ((idx = buffer.indexOf("\n")) >= 0) {
            const line = buffer.slice(0, idx).replace(/\r$/, "");
            buffer = buffer.slice(idx + 1);
            if (line === "") {
              flush();
              if (finished) {
                await reader.cancel();
                return;
              }
            } else if (line.startsWith(":")) {
              // 心跳/注释 — 忽略
            } else if (line.startsWith("event:")) {
              currentEvent = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
              dataLines.push(line.slice(5).replace(/^\s/, ""));
            }
          }
        }
      } catch (e) {
        if (e.name !== "AbortError") onError && onError(String(e));
      }
    })();

    return { abort: () => ctrl.abort() };
  }

  async function authStatus() {
    return _req("GET", "/api/auth/status");
  }

  async function login(password) {
    const r = await _req("POST", "/api/auth/login", { password });
    if (r && r.token !== undefined) setToken(r.token || "");
    return r;
  }

  async function logout() {
    const r = await _req("POST", "/api/auth/logout", {});
    setToken("");
    return r;
  }

  async function respondToolApproval(id, decision, scope = "once") {
    return _req("POST", `/api/tool_approvals/${encodeURIComponent(id)}`, { decision, scope });
  }

  // 短时间格式化（前端通用）
  function relTime(tsMs) {
    if (!tsMs) return "—";
    const diff = (Date.now() - Number(tsMs)) / 1000;
    if (diff < 60) return `${Math.max(1, Math.floor(diff))}s 前`;
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
    return `${Math.floor(diff / 86400)} 天前`;
  }

  function fmtNumber(n) {
    if (n == null) return "—";
    return Number(n).toLocaleString();
  }

  function fmtCost(n) {
    if (n == null) return "—";
    return Number(n).toFixed(4);
  }

  // 简易下载
  function download(filename, text) {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  window.API = {
    get:  (p)      => _req("GET",    p),
    post: (p, b)   => _req("POST",   p, b),
    put:  (p, b)   => _req("PUT",    p, b),
    del:  (p, b)   => _req("DELETE", p, b),
    authStatus,
    login,
    logout,
    respondToolApproval,
    token,
    setToken,
    chatStream,
    blobUrl,
    relTime,
    fmtNumber,
    fmtCost,
    download,
  };
})();

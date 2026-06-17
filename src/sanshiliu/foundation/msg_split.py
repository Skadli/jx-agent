"""按 <MSG> 拆分 LLM 输出为多条独立消息；channel 层共享此函数。

约定（与 persona/core/style.md 对齐）：
- 默认 sentinel：``<MSG>``
- 代码块（三反引号 ``` 包围）内部的 sentinel **失效**——防止配音脚本被错切。
- 输出每段 strip；空段过滤。
- 没有 sentinel 时默认返回 ``[text.strip()]`` 单元素列表（向后兼容旧逻辑）；
  channel 可显式开启段落兜底，按空行拆分非代码块内容。
- text 为空或全空白时返回空列表。

两种 API：
- ``split_messages(text)`` — 一次性拆分（已 collect 全部输出后处理）
- ``StreamingSplitter`` — 流式拆分（边收边推完整段；代码块未闭合时整段保留到 close）
"""

from __future__ import annotations

# 默认拆分标记；persona/core/style.md 里 LLM 被告知用此标签
DEFAULT_SENTINEL: str = "<MSG>"

# 代码块分隔符（与 markdown 三反引号约定一致）
_FENCE: str = "```"


def _find_next_split(text: str, sentinel: str) -> int:
    """从 text 头开始扫，返回下一个不在代码块内的 sentinel 起始位置；找不到返回 -1。

    扫描时同时追踪 ``` 配对：奇数次出现后处于"代码块内"，sentinel 失效。
    """
    pos = 0
    in_code = False
    n = len(text)
    fl = len(_FENCE)
    while pos < n:
        if text.startswith(_FENCE, pos):
            in_code = not in_code
            pos += fl
            continue
        if not in_code and text.startswith(sentinel, pos):
            return pos
        pos += 1
    return -1


def split_messages(
    text: str,
    *,
    sentinel: str = DEFAULT_SENTINEL,
    paragraph_fallback: bool = False,
) -> list[str]:
    """把 text 按 sentinel 拆成多条消息；代码块内 sentinel 失效。

    一次性 API。若代码块未闭合，剩余段全部作为最后一条返回（不丢字）。
    """
    if not text or not text.strip():
        return []
    if sentinel not in text:
        if paragraph_fallback:
            parts = _split_paragraphs_outside_code(text)
            if len(parts) > 1:
                return parts
        return [text.strip()]

    out: list[str] = []
    cur = text
    while True:
        pos = _find_next_split(cur, sentinel)
        if pos < 0:
            break
        seg = cur[:pos].strip()
        if seg:
            out.append(seg)
        cur = cur[pos + len(sentinel):]
    rest = cur.strip()
    if rest:
        out.append(rest)
    return out


def _split_paragraphs_outside_code(text: str) -> list[str]:
    """按空行拆段，但不拆三反引号代码块内部内容。"""
    out: list[str] = []
    buf: list[str] = []
    in_code = False

    def flush() -> None:
        seg = "\n".join(buf).strip()
        if seg:
            out.append(seg)
        buf.clear()

    for line in text.splitlines():
        if not in_code and not line.strip():
            flush()
            continue
        buf.append(line)

        # A line may technically contain more than one fence; toggle once per
        # occurrence so inline examples still keep the parser consistent.
        search_from = 0
        while True:
            pos = line.find(_FENCE, search_from)
            if pos < 0:
                break
            in_code = not in_code
            search_from = pos + len(_FENCE)

    flush()
    return out


class StreamingSplitter:
    """流式按 sentinel 切段；feed 每个 chunk 返回此次能 flush 的完整段列表。

    使用：
        sp = StreamingSplitter()
        for chunk in stream:
            for seg in sp.feed(chunk):
                yield seg
        for seg in sp.close():
            yield seg

    实现取舍：
    - 进入 in_code 后，**不在代码块结束前 flush**——保证代码块完整作为一段。
      代价：长代码块会延迟到 close 才出（或等下一个非 in_code 的 sentinel）。
    - 流末尾的非 sentinel 尾巴只在 close() 时 flush。
    - 跨 chunk 的 ``` 或 sentinel 会被正确处理（依赖 _find_next_split 重新扫描 buf）。
    """

    def __init__(
        self,
        *,
        sentinel: str = DEFAULT_SENTINEL,
        paragraph_fallback: bool = False,
    ) -> None:
        self._sentinel = sentinel
        self._paragraph_fallback = paragraph_fallback
        self._saw_sentinel = False
        self._buf: str = ""
        # feed_stream/close_stream（逐字流式）专用：跨 chunk 记代码块开合状态
        self._in_code = False

    def feed(self, chunk: str) -> list[str]:
        if not chunk:
            return []
        self._buf += chunk
        return self._drain()

    def close(self) -> list[str]:
        """流结束；返回 buffer 剩余段（若有）。"""
        rest = self._buf.strip()
        self._buf = ""
        if not rest:
            return []
        if self._paragraph_fallback and not self._saw_sentinel:
            parts = _split_paragraphs_outside_code(rest)
            if len(parts) > 1:
                return parts
        return [rest]

    def _drain(self) -> list[str]:
        out: list[str] = []
        while True:
            pos = _find_next_split(self._buf, self._sentinel)
            if pos < 0:
                # 没有可 split 的 sentinel：可能未到、可能在未闭合的代码块内；都 break 等下一个 chunk
                break
            seg = self._buf[:pos].strip()
            if seg:
                out.append(seg)
            self._buf = self._buf[pos + len(self._sentinel):]
            self._saw_sentinel = True
        return out

    # ─────────── 逐字流式 API（与 feed/close 并存，二选一用） ───────────
    # feed/close 把整段缓冲到 sentinel/close 才出（前端一次性看到整条气泡）；
    # feed_stream/close_stream 则尽快逐 token 吐文本，只把"可能是半截 <MSG>/```"的极短尾巴
    # （≤4 字符）留到下次，遇代码块外 sentinel 吐 break。供 web SSE / repl 做真·逐字流式。

    def _is_token_prefix(self, tail: str) -> bool:
        """tail 是否为 <MSG> 或 ``` 的"严格非空前缀"（即可能是被切到一半的特殊标记）。"""
        if not tail:
            return False
        return any(
            len(tail) < len(tok) and tok.startswith(tail)
            for tok in (self._sentinel, _FENCE)
        )

    def _drain_stream(self, *, closing: bool) -> list[tuple[str, str]]:
        """扫 buf：尽量吐 ("text", 片段) 与 ("break", "")；非 closing 时末尾保留半截标记。"""
        out: list[tuple[str, str]] = []
        buf = self._buf
        sent = self._sentinel
        fl = len(_FENCE)
        sl = len(sent)
        # closing 时不再有后续 chunk，半截标记按字面文本吐出；故 holdback=0
        holdback = 0 if closing else max(fl, sl) - 1
        text: list[str] = []
        i = 0
        n = len(buf)
        while i < n:
            if buf.startswith(_FENCE, i):
                text.append(_FENCE)
                self._in_code = not self._in_code
                i += fl
                continue
            if not self._in_code and buf.startswith(sent, i):
                if text:
                    out.append(("text", "".join(text)))
                    text = []
                out.append(("break", ""))
                self._saw_sentinel = True
                i += sl
                continue
            # 末尾 holdback 区内若像半截特殊标记，停下来留到下次 feed_stream（避免吐出半个 <MSG>）
            if (n - i) <= holdback and self._is_token_prefix(buf[i:]):
                break
            text.append(buf[i])
            i += 1
        if text:
            out.append(("text", "".join(text)))
        self._buf = buf[i:]
        return out

    def feed_stream(self, chunk: str) -> list[tuple[str, str]]:
        """增量喂入；返回本次能确定的事件序列：("text", 片段) / ("break", "")。"""
        if not chunk:
            return []
        self._buf += chunk
        return self._drain_stream(closing=False)

    def close_stream(self) -> list[tuple[str, str]]:
        """流结束；把保留的尾巴（半截标记此时已确定不再补全）作为纯文本吐出。"""
        events = self._drain_stream(closing=True)
        self._buf = ""
        return events

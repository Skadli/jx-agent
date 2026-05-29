"""web_search：多 provider 自动 fallback；纯文本结果给 LLM。

provider 链（默认 auto）：
  1. Tavily（如有 key）—— 国际 SaaS，结果质量高，需付费
  2. Sogou —— 国内可达、不需 key、对爬虫友好，cn 网络下首选
  3. DuckDuckGo —— 国际兜底，国内被墙时形同摆设

任何 provider 返非空就 return；全部失败才 is_error=True。
环境变量 SANSHILIU_WEB_SEARCH_PROVIDER 可强制单 provider（tavily/sogou/ddg/auto）。
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from sanshiliu.foundation.logging import get_logger
from sanshiliu.tools.types import FunctionTool, ToolDef, ToolResult

_logger = get_logger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_SOGOU_URL = "https://www.sogou.com/web"
_DDG_URL = "https://html.duckduckgo.com/html/"
_RESULT_TOP_N = 5

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(html_frag: str) -> str:
    return _WS_RE.sub(" ", _HTML_TAG_RE.sub("", html_frag)).strip()


# ────────── Provider: Tavily ──────────

async def _tavily_search(query: str, api_key: str) -> str:
    # 连接快速失败：国内裸网络 connect 长时间挂起没意义，5s 没握上手就 fall through
    timeout = httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(
            _TAVILY_URL,
            json={"api_key": api_key, "query": query, "max_results": _RESULT_TOP_N},
        )
    if r.status_code != 200:
        raise RuntimeError(f"Tavily 返回 {r.status_code}: {r.text[:200]}")
    data = r.json()
    items = data.get("results") or []
    if not items:
        return ""
    out: list[str] = [f"[Tavily] 查询：{query}\n"]
    for i, item in enumerate(items[:_RESULT_TOP_N], start=1):
        out.append(
            f"{i}. {item.get('title','')}\n"
            f"   {item.get('content','')[:300]}\n"
            f"   {item.get('url','')}"
        )
    return "\n\n".join(out)


# ────────── Provider: Sogou（国内可达） ──────────

# Sogou 把每条结果包在 <div class="vrwrap"> 里；title 在 <h3 class="vr-title"> 的 <a>
# snippet 候选多个 class（不同结果模板不同），按优先级试
_SOGOU_BLOCK_RE = re.compile(
    r'<div class="vrwrap[^"]*"[^>]*>(.*?)(?=<div class="vrwrap|<div id="page"|$)',
    re.DOTALL,
)
_SOGOU_TITLE_RE = re.compile(
    r'<h3[^>]*class="[^"]*vr-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_SOGOU_SNIPPET_PATS = [
    re.compile(r'<div class="text-layout[^"]*"[^>]*>(.*?)</div>', re.DOTALL),
    re.compile(r'<p class="star-wiki[^"]*"[^>]*>(.*?)</p>', re.DOTALL),
    re.compile(r'<div class="fz-mid space-txt[^"]*"[^>]*>(.*?)</div>', re.DOTALL),
    re.compile(r'<div class="[^"]*\bft\b[^"]*"[^>]*>(.*?)</div>', re.DOTALL),
    re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL),
]


async def _sogou_search(query: str) -> str:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
        r = await c.get(
            _SOGOU_URL,
            params={"query": query, "ie": "utf8"},
            headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        )
    if r.status_code != 200:
        raise RuntimeError(f"Sogou 返回 {r.status_code}")
    html = r.text

    results: list[tuple[str, str, str]] = []
    for block in _SOGOU_BLOCK_RE.findall(html):
        m_t = _SOGOU_TITLE_RE.search(block)
        if not m_t:
            continue
        url = m_t.group(1)
        title = _clean(m_t.group(2))
        if not title:
            continue
        snippet = ""
        for pat in _SOGOU_SNIPPET_PATS:
            m_s = pat.search(block)
            if m_s:
                snippet = _clean(m_s.group(1))
                if snippet:
                    break
        results.append((url, title, snippet))
        if len(results) >= _RESULT_TOP_N:
            break

    if not results:
        return ""
    out = [f"[Sogou] 查询：{query}\n"]
    for i, (url, title, snippet) in enumerate(results, start=1):
        out.append(f"{i}. {title}\n   {snippet[:300]}\n   {url}")
    return "\n\n".join(out)


# ────────── Provider: DuckDuckGo（国际兜底） ──────────

_DDG_RESULT_RE = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)


async def _ddg_search(query: str) -> str:
    # DuckDuckGo 在国内被墙，连接通常直接 timeout；compact 的 connect timeout 让它快速 fall through
    timeout = httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        r = await c.post(_DDG_URL, data={"q": query}, headers={"User-Agent": _UA})
    if r.status_code != 200:
        raise RuntimeError(f"DuckDuckGo 返回 {r.status_code}")
    matches = _DDG_RESULT_RE.findall(r.text)
    if not matches:
        return ""
    out = [f"[DuckDuckGo] 查询：{query}\n"]
    for i, (url, title, snippet) in enumerate(matches[:_RESULT_TOP_N], start=1):
        out.append(f"{i}. {title}\n   {_clean(snippet)[:300]}\n   {url}")
    return "\n\n".join(out)


# ────────── Provider chain ──────────

async def _try(provider_name: str, coro_fn: Callable[[], Awaitable[str]]) -> str:
    """跑一个 provider；返回非空字符串视为成功；空 / 异常都视为失败。"""
    try:
        text: str = await coro_fn()
        if text and text.strip():
            return text
        _logger.info("web_search provider 无结果，尝试下一个", provider=provider_name)
    except Exception as exc:
        _logger.warning(
            "web_search provider 失败，尝试下一个",
            provider=provider_name, error=f"{type(exc).__name__}: {exc}",
        )
    return ""


def _provider_chain(tavily_api_key: str | None) -> list[tuple[str, Any]]:
    """根据 env / key 决定 provider 顺序；返回 [(name, async_callable)]。"""
    override = (os.environ.get("SANSHILIU_WEB_SEARCH_PROVIDER") or "").strip().lower()

    def _t(query: str) -> Awaitable[str]: return _tavily_search(query, tavily_api_key or "")
    def _s(query: str) -> Awaitable[str]: return _sogou_search(query)
    def _d(query: str) -> Awaitable[str]: return _ddg_search(query)

    if override == "tavily":
        return [("tavily", _t)]
    if override == "sogou":
        return [("sogou", _s)]
    if override in ("ddg", "duckduckgo"):
        return [("ddg", _d)]

    # auto: 有 tavily key 就先试，否则 sogou（国内友好）→ ddg（国际兜底）
    chain: list[tuple[str, Any]] = []
    if tavily_api_key:
        chain.append(("tavily", _t))
    chain.append(("sogou", _s))
    chain.append(("ddg", _d))
    return chain


def build_web_search_tool(definition: ToolDef, tavily_api_key: str | None = None) -> FunctionTool:
    """工厂；按 provider_chain 顺序试，第一个出非空结果的胜出。"""

    async def _run(args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(
                call_id="", name=definition.name,
                content="参数 query 不能为空", is_error=True,
            )

        chain = _provider_chain(tavily_api_key)
        last_error = ""
        for name, fn in chain:
            def _call(
                fn: Callable[[str], Awaitable[str]] = fn,
                query: str = query,
            ) -> Awaitable[str]:
                return fn(query)
            text = await _try(name, _call)
            if text:
                return ToolResult(call_id="", name=definition.name, content=text)
            last_error = name

        return ToolResult(
            call_id="", name=definition.name,
            content=f"搜索失败：所有 provider 都未返结果（最后尝试：{last_error}）。"
                    f"国内环境建议设 SANSHILIU_WEB_SEARCH_PROVIDER=sogou 或配置 TAVILY_API_KEY。",
            is_error=True,
        )

    return FunctionTool(_def=definition, _fn=_run)


__all__ = ["build_web_search_tool"]

"""web_search：Tavily 优先，无 key 走 DuckDuckGo HTML 兜底；纯文本结果给 LLM。"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from sanshiliu.foundation.logging import get_logger
from sanshiliu.tools.types import FunctionTool, ToolDef, ToolResult

_logger = get_logger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_DDG_URL = "https://html.duckduckgo.com/html/"
_RESULT_TOP_N = 5


async def _tavily_search(query: str, api_key: str) -> str:
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(
            _TAVILY_URL,
            json={"api_key": api_key, "query": query, "max_results": _RESULT_TOP_N},
        )
    if r.status_code != 200:
        raise RuntimeError(f"Tavily 返回 {r.status_code}: {r.text[:200]}")
    data = r.json()
    out: list[str] = [f"[Tavily] 查询：{query}\n"]
    for i, item in enumerate(data.get("results", [])[:_RESULT_TOP_N], start=1):
        out.append(f"{i}. {item.get('title','')}\n   {item.get('content','')[:300]}\n   {item.get('url','')}")
    return "\n\n".join(out)


# 极简 DuckDuckGo HTML 解析；只取结果块的标题+片段，规避 JS 渲染
_DDG_RESULT_RE = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


async def _ddg_search(query: str) -> str:
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
        r = await c.post(_DDG_URL, data={"q": query}, headers={"User-Agent": "sanshiliu/1.0"})
    if r.status_code != 200:
        raise RuntimeError(f"DuckDuckGo 返回 {r.status_code}")
    matches = _DDG_RESULT_RE.findall(r.text)
    if not matches:
        return f"[DuckDuckGo] 查询「{query}」无结果或被反爬"
    out = [f"[DuckDuckGo] 查询：{query}\n"]
    for i, (url, title, snippet) in enumerate(matches[:_RESULT_TOP_N], start=1):
        clean = _HTML_TAG_RE.sub("", snippet).strip()
        out.append(f"{i}. {title}\n   {clean[:300]}\n   {url}")
    return "\n\n".join(out)


def build_web_search_tool(definition: ToolDef, tavily_api_key: str | None = None) -> FunctionTool:
    """工厂；外层注入 Tavily key 决定走哪个 provider。"""

    async def _run(args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(
                call_id="", name=definition.name,
                content="参数 query 不能为空", is_error=True,
            )
        try:
            if tavily_api_key:
                text = await _tavily_search(query, tavily_api_key)
            else:
                text = await _ddg_search(query)
        except Exception as exc:
            _logger.warning("web_search 失败", error=str(exc))
            return ToolResult(
                call_id="", name=definition.name,
                content=f"搜索失败：{type(exc).__name__}: {exc}", is_error=True,
            )
        return ToolResult(call_id="", name=definition.name, content=text)

    return FunctionTool(_def=definition, _fn=_run)


# 用于单测直接验证 _ddg_search HTML 解析逻辑
__all__ = ["build_web_search_tool"]


def _unused() -> None:
    """避免 json 未使用警告（保留 import 以便后续扩展返回结构化数据）。"""
    json.dumps({})

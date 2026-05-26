"""OpenAI 兼容 LLM 客户端；负责流式输出、retry 和 llm_calls 落表。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from sanshiliu.foundation.errors import LLMFatalError, LLMRetryableError
from sanshiliu.foundation.logging import get_logger
from sanshiliu.foundation.retry import async_retry
from sanshiliu.llm.cost import estimate_cost
from sanshiliu.llm.stream import StreamDelta, StreamResult
from sanshiliu.storage.db import Database

_logger = get_logger(__name__)

# OpenAI SDK 可重试异常
_RETRYABLE_OPENAI_EXC: tuple[type[Exception], ...] = (
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
)
# httpx 瞬时网络错误
_RETRYABLE_HTTPX_EXC: tuple[type[Exception], ...] = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
)
_FATAL_OPENAI_EXC: tuple[type[Exception], ...] = (
    AuthenticationError,
    BadRequestError,
)


def _humanize_fatal(exc: Exception, model: str) -> str:
    """致命错误 → 人话错误信息；image_url 被拒的 deserialize 报文翻译成可操作的提示。

    典型反例：Ark/DeepSeek 把 messages.content 反序列化为 enum，文本模型只识 `text`
    变体，遇到 `image_url` 报 "unknown variant `image_url`, expected `text`"，
    原文用户看不懂，包装一层提示去换支持视觉的模型。
    """
    raw = f"{type(exc).__name__}: {exc}"
    text = str(exc).lower()
    if "image_url" in text and ("unknown variant" in text or "expected `text`" in text or "expected \"text\"" in text):
        return (
            f"LLM 致命错误：当前模型 `{model}` 不支持图片输入（image_url）。"
            f"请把模型换成支持视觉的型号（如 doubao-1-5-vision-pro-32k-250115、"
            f"doubao-seed-1-6-250615、gpt-4o），或本轮不附图片。"
            f"原始错误：{raw}"
        )
    return f"LLM 致命错误：{raw}"


class LLMClient:
    """OpenAI 兼容 LLM 客户端封装。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        db: Database | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._db = db
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            # 禁用 SDK retry，避免双重退避
            max_retries=0,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    async def _open_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int | None,
    ) -> Any:
        """裸开流并映射异常；retry 包在 chat 整次调用外层。"""
        # OpenAI 2.x TypedDict 过细，这里按兼容子集传 dict 并集中忽略 overload
        try:
            return await self._client.chat.completions.create(  # type: ignore[call-overload]
                model=self._model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
            )
        except _RETRYABLE_OPENAI_EXC as exc:
            raise LLMRetryableError(f"LLM 可重试错误：{type(exc).__name__}: {exc}") from exc
        except _FATAL_OPENAI_EXC as exc:
            raise LLMFatalError(_humanize_fatal(exc, self._model)) from exc
        except APIError as exc:
            # 未细分 APIError 按致命处理
            raise LLMFatalError(f"LLM API 未分类错误：{exc}") from exc

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        session_id: str,
        channel: str,
        user_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_retries_on_disconnect: int = 2,
    ) -> AsyncIterator[StreamDelta]:
        """流式 yield 增量；首字前断连可重试；整次调用只落 1 行 llm_calls。"""
        attempts_left = max_retries_on_disconnect + 1
        outer_start = time.monotonic()
        # 跨重试累计：用最后一次尝试的统计；只在成功或最终失败时落表 1 次
        final_input_tokens = 0
        final_output_tokens = 0
        final_stop_reason: str | None = None
        final_err: str | None = None
        succeeded = False

        try:
            while attempts_left > 0:
                attempts_left -= 1
                stop_reason: str | None = None
                input_tokens = 0
                output_tokens = 0
                yielded_any = False

                try:
                    stream = await self._open_stream(
                        messages,
                        tools=tools,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    async for chunk in stream:
                        if chunk.choices:
                            choice = chunk.choices[0]
                            content = getattr(choice.delta, "content", None) or ""
                            if content:
                                yielded_any = True
                                yield StreamDelta(text=content)
                            if choice.finish_reason:
                                stop_reason = choice.finish_reason
                        usage = getattr(chunk, "usage", None)
                        if usage is not None:
                            input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                            output_tokens = getattr(usage, "completion_tokens", 0) or 0
                    final_input_tokens = input_tokens
                    final_output_tokens = output_tokens
                    final_stop_reason = stop_reason
                    final_err = None
                    succeeded = True
                    return
                except _RETRYABLE_HTTPX_EXC as exc:
                    final_err = f"{type(exc).__name__}: {exc}"
                    # 已输出过字符就不能重试（避免重复）；首字前断连且还有 attempts 才重试
                    if yielded_any or attempts_left <= 0:
                        _logger.error(
                            "流式中断（无法重试）",
                            yielded_any=yielded_any,
                            attempts_left=attempts_left,
                            error=final_err,
                        )
                        raise LLMRetryableError(f"流式中断：{final_err}") from exc
                    _logger.warning(
                        "首字前断连，整次重试",
                        attempts_left=attempts_left,
                        error=final_err,
                    )
                    continue
                except Exception as exc:
                    final_err = f"{type(exc).__name__}: {exc}"
                    raise
        finally:
            # 整个生命周期只落 1 行；error 优先于 success；不区分中间尝试
            await self._record(
                session_id=session_id,
                channel=channel,
                user_id=user_id,
                input_tokens=final_input_tokens,
                output_tokens=final_output_tokens,
                latency_ms=int((time.monotonic() - outer_start) * 1000),
                stop_reason=final_stop_reason if succeeded else None,
                error=final_err,
            )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        session_id: str,
        channel: str,
        user_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> StreamResult:
        """非流式入口；内部走流式 API 并对整次调用自动重试。"""
        return await self._chat_with_retry(
            messages=messages,
            session_id=session_id,
            channel=channel,
            user_id=user_id,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @async_retry(
        max_attempts=4,
        base=0.5,
        cap=8.0,
        jitter=0.3,
        retry_on=LLMRetryableError,
    )
    async def _chat_with_retry(
        self,
        *,
        messages: list[dict[str, Any]],
        session_id: str,
        channel: str,
        user_id: str | None,
        tools: list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int | None,
    ) -> StreamResult:
        """单次 chat 尝试；瞬时错误转成 LLMRetryableError 给外层 retry。"""
        start = time.monotonic()
        text_chunks: list[str] = []
        stop_reason: str | None = None
        input_tokens = 0
        output_tokens = 0
        err_text: str | None = None
        # Phase 5 起：累积流式 tool_calls；按 delta.tool_calls[*].index 归并
        tc_accum: dict[int, dict[str, Any]] = {}
        # DeepSeek reasoner 系列 thinking mode 会返 delta.reasoning_content；需原样回传
        reasoning_chunks: list[str] = []

        try:
            stream = await self._open_stream(
                messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            async for chunk in stream:
                if chunk.choices:
                    choice = chunk.choices[0]
                    content = getattr(choice.delta, "content", None) or ""
                    if content:
                        text_chunks.append(content)
                    rc = getattr(choice.delta, "reasoning_content", None) or ""
                    if rc:
                        reasoning_chunks.append(rc)
                    delta_tcs = getattr(choice.delta, "tool_calls", None) or []
                    for dtc in delta_tcs:
                        # OpenAI 流：delta.tool_calls[*] 每片有 index、可能含 id/type/function.name/arguments 片段
                        idx = getattr(dtc, "index", None)
                        if idx is None:
                            continue
                        slot = tc_accum.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if getattr(dtc, "id", None):
                            slot["id"] = dtc.id
                        fn = getattr(dtc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                slot["function"]["name"] += fn.name
                            if getattr(fn, "arguments", None):
                                slot["function"]["arguments"] += fn.arguments
                    if choice.finish_reason:
                        stop_reason = choice.finish_reason
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    output_tokens = getattr(usage, "completion_tokens", 0) or 0
        except _RETRYABLE_HTTPX_EXC as exc:
            # 丢弃半截结果，交给外层 retry
            err_text = f"{type(exc).__name__}: {exc}"
            await self._record(
                session_id=session_id,
                channel=channel,
                user_id=user_id,
                input_tokens=0,
                output_tokens=0,
                latency_ms=int((time.monotonic() - start) * 1000),
                stop_reason=None,
                error=err_text,
            )
            raise LLMRetryableError(f"流消费中断：{err_text}") from exc
        except Exception as exc:
            err_text = f"{type(exc).__name__}: {exc}"
            await self._record(
                session_id=session_id,
                channel=channel,
                user_id=user_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=int((time.monotonic() - start) * 1000),
                stop_reason=None,
                error=err_text,
            )
            raise

        latency_ms = int((time.monotonic() - start) * 1000)
        await self._record(
            session_id=session_id,
            channel=channel,
            user_id=user_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            stop_reason=stop_reason,
            error=None,
        )
        tool_calls = [tc_accum[i] for i in sorted(tc_accum.keys())]
        return StreamResult(
            text="".join(text_chunks),
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            tool_calls=tool_calls,
            reasoning_content="".join(reasoning_chunks),
        )

    async def _record(
        self,
        *,
        session_id: str,
        channel: str,
        user_id: str | None,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        stop_reason: str | None,
        error: str | None,
    ) -> None:
        """落 llm_calls 表（验收 1-V5）。db=None 时只打日志、不抛。

        每次调用都打一行 INFO（成功）或 WARNING（失败），含模型 / base_url /
        token / 成本 / 延迟，方便 stdout/journalctl 直接看到调用流。
        """
        cost = estimate_cost(self._model, input_tokens, output_tokens)
        log_fields = {
            "model": self._model,
            "base_url": self._base_url,
            "session": (session_id[:8] + "...") if len(session_id) > 8 else session_id,
            "channel": channel,
            "in_tok": input_tokens,
            "out_tok": output_tokens,
            "total_tok": input_tokens + output_tokens,
            "cost_cny": round(cost, 6),
            "latency_ms": latency_ms,
            "stop": stop_reason,
        }
        if error:
            _logger.warning("LLM 调用失败", error=error[:200], **log_fields)
        else:
            _logger.info("LLM 调用完成", **log_fields)

        if self._db is None:
            return
        try:
            await self._db.insert_llm_call(
                session_id=session_id,
                channel=channel,
                user_id=user_id,
                model=self._model,
                base_url=self._base_url,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_cny=cost,
                latency_ms=latency_ms,
                stop_reason=stop_reason,
                error=error,
            )
        except Exception as exc:
            # 落表失败仅记日志，不阻塞主对话
            _logger.error("落 llm_calls 失败（不阻塞主流程）", error=str(exc))

    async def close(self) -> None:
        await self._client.close()

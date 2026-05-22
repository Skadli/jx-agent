"""LLM 客户端：AsyncOpenAI 包装 + 流式 + 自动 retry + 落表。

设计要点：
- 走 OpenAI 兼容标准子集：``chat.completions.create(stream=True)``。
- 同一份代码可走官方 / DeepSeek / GLM / OneAPI / Ollama，base_url 切换即可（验收 1-V3）。
- openai SDK 抛的异常映射到我们的 ``LLMRetryableError`` / ``LLMFatalError``；
  retry 装饰器只对可重试异常退避（验收 1-V6）。
- 每次调用结束（成功或失败）都写一行 ``llm_calls``，含 ``base_url`` 字段（验收 1-V5）。
- 两种入口：``stream_chat`` 真流式 yield delta；``chat`` 一次性拿完整 :class:`StreamResult`。
"""

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

# openai SDK 异常 → 我们的语义分类
_RETRYABLE_OPENAI_EXC: tuple[type[Exception], ...] = (
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
)
# httpx 层的瞬时网络错误（流中途断连最常见的就是 RemoteProtocolError）
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


class LLMClient:
    """OpenAI 兼容 LLM 客户端。

    流式用法::

        async for delta in client.stream_chat(messages, session_id="s1", channel="repl"):
            print(delta.text, end="", flush=True)

    一次性用法::

        result = await client.chat(messages, session_id="s1", channel="repl")
        print(result.text, result.input_tokens, result.output_tokens)
    """

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
            # 退避交给我们自己；SDK 内置 retry 关掉防止双重退避
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
        """裸调用 openai SDK 拿到流；异常映射到我们的体系。

        注意：这里**不挂 retry 装饰器**——retry 包在 :meth:`chat` 整次调用上，
        这样流中途断连（``httpx.RemoteProtocolError``）也能被重试。
        """
        # openai 2.x 类型用了精细 TypedDict；我们走 dict 通用约定（OpenAI 兼容标准子集），
        # 此处一次性 ignore call-overload 是有意为之，详见 prd 1.1 决定记录。
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
            raise LLMFatalError(f"LLM 致命错误：{type(exc).__name__}: {exc}") from exc
        except APIError as exc:
            # 兜底：未细分的 APIError 按致命处理
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
        """真·流式：增量到了就 yield，结束（不论成功失败）都落 llm_calls。

        当流**未输出任何字符**就断连时（``httpx.RemoteProtocolError`` 等），
        自动整次重试 ``max_retries_on_disconnect`` 次——这是用户能感知的最大好处。
        若已 yield 部分字符再断连，则不重试（避免对用户重复输出）。
        """
        attempts_left = max_retries_on_disconnect + 1
        while attempts_left > 0:
            attempts_left -= 1
            start = time.monotonic()
            stop_reason: str | None = None
            input_tokens = 0
            output_tokens = 0
            err_text: str | None = None
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
                # 正常结束 → 落表后返回
                await self._record(
                    session_id=session_id,
                    channel=channel,
                    user_id=user_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    stop_reason=stop_reason,
                    error=None,
                )
                return
            except _RETRYABLE_HTTPX_EXC as exc:
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
                # 已输出过字符就不能再重试（会重复输出）；首字之前断连则可重试
                if yielded_any or attempts_left <= 0:
                    _logger.error(
                        "流式中断（无法重试）",
                        yielded_any=yielded_any,
                        attempts_left=attempts_left,
                        error=err_text,
                    )
                    raise LLMRetryableError(f"流式中断：{err_text}") from exc
                _logger.warning(
                    "首字前断连，整次重试",
                    attempts_left=attempts_left,
                    error=err_text,
                )
                continue
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
        """非流式入口：内部走流式 API 但**整次调用**自动重试（含流消费中途失败）。

        engine 层若不需要边收边显，用这个最方便。
        """
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
        """单次 chat 尝试（含开流 + 消费）；任何 httpx/openai 瞬时错误抛 LLMRetryableError 给外层 retry。"""
        start = time.monotonic()
        text_chunks: list[str] = []
        stop_reason: str | None = None
        input_tokens = 0
        output_tokens = 0
        err_text: str | None = None

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
                    if choice.finish_reason:
                        stop_reason = choice.finish_reason
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    output_tokens = getattr(usage, "completion_tokens", 0) or 0
        except _RETRYABLE_HTTPX_EXC as exc:
            # 流中途 httpx 断连——丢弃半截结果，触发外层 retry
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
        return StreamResult(
            text="".join(text_chunks),
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
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
        """落 llm_calls 表（验收 1-V5）。db=None 时只打日志、不抛。"""
        cost = estimate_cost(self._model, input_tokens, output_tokens)
        if self._db is None:
            _logger.debug(
                "LLM 调用完成（无 DB，跳过落表）",
                model=self._model,
                tokens=(input_tokens, output_tokens),
                cost_cny=cost,
                latency_ms=latency_ms,
                error=error,
            )
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
            # 不阻塞主对话：落表失败仅记日志，调用方拿到的仍是正常 result
            _logger.error("落 llm_calls 失败（不阻塞主流程）", error=str(exc))

    async def close(self) -> None:
        await self._client.close()

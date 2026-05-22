"""全局配置（pydantic-settings）。

设计原则：
- 必填字段缺失立即启动失败，错误信息含具体字段名（验收项 1-V2）。
- 所有字段都有默认值或必填校验；运行期不写回环境变量。
- 单例：通过 :func:`get_settings` 取，第一次调用做加载和校验。
- data_dir / home_dir 自动创建，避免 Phase 9 之前各模块到处 mkdir。

环境变量优先级：进程 env > .env 文件 > 字段默认值。

env 命名约定：
- OPENAI_*  直接读取（与 openai SDK 习惯一致，不加前缀）
- SANSHILIU_* / ILINK_*  通过 AliasChoices 显式声明，避免污染字段名
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """全局配置容器。"""

    # ── LLM（OpenAI 兼容标准子集） ───────────────────────────
    openai_api_key: SecretStr = Field(
        ...,
        description="OpenAI 兼容后端 API Key；缺则启动失败",
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI 兼容后端 base URL；改此字段可切到 DeepSeek/GLM/Ollama 等",
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        description="模型 ID；与 base_url 后端约定",
    )

    # ── 运行时 ─────────────────────────────────────────────
    data_dir: Path = Field(
        default=Path("./data"),
        validation_alias=AliasChoices("data_dir", "SANSHILIU_DATA_DIR"),
        description="本地数据目录（sqlite、日志、jsonl 落盘）；不存在会自动创建",
    )
    home_dir: Path = Field(
        default_factory=lambda: Path.home() / ".sanshiliu",
        validation_alias=AliasChoices("home_dir", "SANSHILIU_HOME_DIR"),
        description="用户级数据目录（CLAUDE.md、memdir、skills、settings.json 等）",
    )
    max_context_tokens: int = Field(
        default=128_000,
        ge=4_000,
        le=2_000_000,
        validation_alias=AliasChoices("max_context_tokens", "SANSHILIU_MAX_CONTEXT_TOKENS"),
        description="对话上下文最大 token；命中阈值（默认 80%）触发 compact",
    )
    compact_threshold_ratio: float = Field(
        default=0.8,
        gt=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "compact_threshold_ratio", "SANSHILIU_COMPACT_THRESHOLD_RATIO"
        ),
        description="compact 触发阈值：当前 token / max * ratio",
    )
    log_level: LogLevel = Field(
        default="INFO",
        validation_alias=AliasChoices("log_level", "SANSHILIU_LOG_LEVEL"),
        description="日志级别（控制台 + JSONL 同步生效）",
    )

    # ── 通道开关（Phase 4 启用） ────────────────────────────
    wechat_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("wechat_enabled", "SANSHILIU_WECHAT_ENABLED"),
        description="是否启用 iLink 微信 bot",
    )
    web_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("web_enabled", "SANSHILIU_WEB_ENABLED"),
        description="是否启用 HTTP 服务",
    )
    web_port: int = Field(
        default=9527,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("web_port", "SANSHILIU_WEB_PORT"),
        description="HTTP 服务监听端口",
    )

    # ── iLink 微信 Bot（Phase 4 启用时必填） ────────────────
    ilink_base_url: str = Field(
        default="http://127.0.0.1:8080",
        validation_alias=AliasChoices("ilink_base_url", "ILINK_BASE_URL"),
        description="iLink HTTP 地址",
    )
    ilink_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ilink_api_key", "ILINK_API_KEY"),
        description="iLink API Key",
    )
    ilink_webhook_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ilink_webhook_secret", "ILINK_WEBHOOK_SECRET"),
        description="iLink webhook HMAC 签名密钥",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # 允许字段同时通过 alias 和 field name 填充
        populate_by_name=True,
    )

    @field_validator("data_dir", "home_dir", mode="after")
    @classmethod
    def _ensure_dir_exists(cls, v: Path) -> Path:
        """缺目录就建；data_dir 必须能写。避免后续各模块到处 mkdir。"""
        v = v.expanduser().resolve()
        v.mkdir(parents=True, exist_ok=True)
        return v

    @field_validator("openai_base_url", mode="after")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        """base_url 末尾不带 /，与 openai SDK 内部拼接一致，避免双斜杠 404。"""
        return v.rstrip("/")

    @model_validator(mode="after")
    def _check_channel_dependencies(self) -> Settings:
        """通道启用时校验对应凭据，失败信息明确字段名（验收 1-V2 同款）。"""
        if self.wechat_enabled:
            missing: list[str] = []
            if not self.ilink_api_key:
                missing.append("ILINK_API_KEY")
            if not self.ilink_webhook_secret:
                missing.append("ILINK_WEBHOOK_SECRET")
            if missing:
                raise ValueError(
                    f"wechat_enabled=true 但缺少凭据：{', '.join(missing)}；"
                    f"请在 .env 中补齐或将 SANSHILIU_WECHAT_ENABLED 设为 false"
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局单例。第一次调用做加载与校验，之后从缓存返回。

    在测试中需要重置：调用 :func:`get_settings.cache_clear()`。
    """
    try:
        return Settings()  # type: ignore[call-arg]  # pydantic-settings 运行时填充
    except Exception:
        # 启动失败让 ValidationError 原样上抛——pydantic 的错误信息已含字段名
        logging.getLogger(__name__).exception("配置加载失败")
        raise

"""全局配置；env 优先于 .env，缺必填字段会带字段名启动失败。"""

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

    # LLM 配置
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

    # 运行时配置
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
    persona_dir: Path = Field(
        default=Path("./persona"),
        validation_alias=AliasChoices("persona_dir", "SANSHILIU_PERSONA_DIR"),
        description="人设 markdown 目录；5 份 md 必须齐",
    )
    prompts_dir: Path = Field(
        default=Path("./prompts"),
        validation_alias=AliasChoices("prompts_dir", "SANSHILIU_PROMPTS_DIR"),
        description="系统级 prompts md 目录；compact/microcompact 指令存放处",
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

    # 通道开关，Phase 4 启用
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

    # iLink 微信 Bot，Phase 4 启用时必填
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
    ilink_signature_header: str = Field(
        default="X-iLink-Signature",
        validation_alias=AliasChoices("ilink_signature_header", "ILINK_SIGNATURE_HEADER"),
        description="iLink webhook HMAC 签名 header 名",
    )

    # 微信白名单 + 限流 + 黑名单；CSV 形式
    wechat_whitelist: str = Field(
        default="",
        validation_alias=AliasChoices("wechat_whitelist", "SANSHILIU_WECHAT_WHITELIST"),
        description="逗号分隔的 wxid 白名单；空集合 = 一律拒绝",
    )
    wechat_input_blacklist: str = Field(
        default="",
        validation_alias=AliasChoices("wechat_input_blacklist", "SANSHILIU_WECHAT_INPUT_BLACKLIST"),
        description="逗号分隔的输入关键词；命中则不回复",
    )
    wechat_output_blacklist: str = Field(
        default="",
        validation_alias=AliasChoices("wechat_output_blacklist", "SANSHILIU_WECHAT_OUTPUT_BLACKLIST"),
        description="逗号分隔的输出关键词；命中则替换为话术",
    )
    wechat_rate_per_user_per_day: int = Field(
        default=30, ge=1, le=10_000,
        validation_alias=AliasChoices(
            "wechat_rate_per_user_per_day", "SANSHILIU_WECHAT_RATE_PER_USER_PER_DAY",
        ),
        description="单用户每日额度；超过收冷却提示",
    )
    wechat_rate_global_per_minute: int = Field(
        default=2, ge=1, le=1_000,
        validation_alias=AliasChoices(
            "wechat_rate_global_per_minute", "SANSHILIU_WECHAT_RATE_GLOBAL_PER_MINUTE",
        ),
        description="全局每分钟额度；保护后端突发流量",
    )

    # Phase 5 工具配置
    tools_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("tools_enabled", "SANSHILIU_TOOLS_ENABLED"),
        description="是否启用 tool_calls；关掉就回到 Phase 4 行为",
    )
    tavily_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("tavily_api_key", "TAVILY_API_KEY"),
        description="Tavily 搜索 key；缺则 web_search 走 DuckDuckGo HTML 兜底",
    )

    # Phase 6 skills 配置
    skills_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("skills_enabled", "SANSHILIU_SKILLS_ENABLED"),
        description="是否启用 SKILL.md 加载与匹配",
    )
    skills_dir_project: Path = Field(
        default=Path("./.sanshiliu/skills"),
        validation_alias=AliasChoices("skills_dir_project", "SANSHILIU_SKILLS_DIR_PROJECT"),
        description="项目级 skills 目录（优先级最高）",
    )
    skills_dir_repo: Path = Field(
        default=Path("./skills"),
        validation_alias=AliasChoices("skills_dir_repo", "SANSHILIU_SKILLS_DIR_REPO"),
        description="仓库内自带 skills 目录（优先级最低）",
    )

    # Phase 8 安全权限配置
    security_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("security_enabled", "SANSHILIU_SECURITY_ENABLED"),
        description="是否启用 settings.json 权限审批；关闭后所有工具直接放行",
    )

    # Phase 7 长期记忆配置
    memory_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("memory_enabled", "SANSHILIU_MEMORY_ENABLED"),
        description="是否启用 CLAUDE.md + memdir 加载",
    )
    memdir_dir: Path = Field(
        default=Path("./memdir"),
        validation_alias=AliasChoices("memdir_dir", "SANSHILIU_MEMDIR_DIR"),
        description="memdir 根目录；含 MEMORY.md 索引与 4 类记忆 md",
    )
    auto_extract_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("auto_extract_enabled", "SANSHILIU_AUTO_EXTRACT_ENABLED"),
        description="是否在每轮对话后异步调 LLM 提取候选记忆（默认关；按需开）",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # alias 和字段名都可填充
        populate_by_name=True,
    )

    @field_validator("data_dir", "home_dir", mode="after")
    @classmethod
    def _ensure_dir_exists(cls, v: Path) -> Path:
        """缺目录就建；data_dir/home_dir 必须能写。"""
        v = v.expanduser().resolve()
        v.mkdir(parents=True, exist_ok=True)
        return v

    @field_validator("persona_dir", "prompts_dir", mode="after")
    @classmethod
    def _resolve_markup_dir(cls, v: Path) -> Path:
        """persona_dir/prompts_dir 只解析路径不强建——对应 loader 内会校验文件齐不齐。"""
        return v.expanduser().resolve()

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
    """全局单例；首次加载并校验，测试可调用 cache_clear 重置。"""
    try:
        return Settings()  # type: ignore[call-arg]  # pydantic-settings 运行时填充
    except Exception:
        # 保留 pydantic 原始字段错误信息
        logging.getLogger(__name__).exception("配置加载失败")
        raise

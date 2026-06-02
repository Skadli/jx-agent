"""全局配置；env 优先于 .env，缺必填字段会带字段名启动失败。"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """全局配置容器。"""

    # LLM 配置（默认值按国内可达后端 DeepSeek 设置；改 .env 可切到 OpenAI/GLM/Ollama 等）
    openai_api_key: SecretStr = Field(
        ...,
        description="OpenAI 兼容后端 API Key；缺则启动失败",
    )
    openai_base_url: str = Field(
        default="https://api.deepseek.com",
        description="OpenAI 兼容后端 base URL；国内默认 DeepSeek，海外可改成 https://api.openai.com/v1",
    )
    openai_model: str = Field(
        default="deepseek-chat",
        description="模型 ID；与 base_url 后端约定（DeepSeek 稳定别名 deepseek-chat / deepseek-reasoner）",
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
    dashboard_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("dashboard_password", "SANSHILIU_DASHBOARD_PASSWORD"),
        description="Dashboard 首次进入密码；为空时不启用面板门禁",
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

    # Hermes 风格官方 iLink Bot 凭据；扫码后由 setup 自动写入
    weixin_account_id: str = Field(
        default="",
        validation_alias=AliasChoices("weixin_account_id", "WEIXIN_ACCOUNT_ID"),
        description="iLink 官方 Bot 账号 ID",
    )
    weixin_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("weixin_token", "WEIXIN_TOKEN"),
        description="iLink 官方 Bot token",
    )
    weixin_base_url: str = Field(
        default="https://ilinkai.weixin.qq.com",
        validation_alias=AliasChoices("weixin_base_url", "WEIXIN_BASE_URL"),
        description="iLink 官方 Bot API 地址",
    )
    weixin_poll_timeout_ms: int = Field(
        default=35_000,
        ge=5_000,
        le=120_000,
        validation_alias=AliasChoices("weixin_poll_timeout_ms", "WEIXIN_POLL_TIMEOUT_MS"),
        description="iLink 官方 Bot getupdates 长轮询超时",
    )
    weixin_poll_interval_ms: int = Field(
        default=1_000,
        ge=100,
        le=60_000,
        validation_alias=AliasChoices("weixin_poll_interval_ms", "WEIXIN_POLL_INTERVAL_MS"),
        description="iLink 官方 Bot 轮询间隔",
    )

    # 微信白名单 + 限流 + 黑名单；CSV 形式
    wechat_whitelist: str = Field(
        default="",
        validation_alias=AliasChoices("wechat_whitelist", "SANSHILIU_WECHAT_WHITELIST"),
        description="逗号分隔的 wxid 白名单；空集合 = 允许所有微信用户",
    )
    wechat_input_blacklist: str = Field(
        default="",
        validation_alias=AliasChoices("wechat_input_blacklist", "SANSHILIU_WECHAT_INPUT_BLACKLIST"),
        description="逗号分隔的输入关键词；命中则不回复",
    )
    wechat_output_blacklist: str = Field(
        default="",
        validation_alias=AliasChoices(
            "wechat_output_blacklist", "SANSHILIU_WECHAT_OUTPUT_BLACKLIST"
        ),
        description="逗号分隔的输出关键词；命中则替换为话术",
    )

    # Phase 10 豆包多模态后端（可选；缺则 router 降级单后端走 openai_*）
    doubao_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("doubao_api_key", "DOUBAO_API_KEY"),
        description="火山引擎 Ark API Key；缺则不注册豆包 provider，纯文本场景不受影响",
    )
    doubao_base_url: str = Field(
        default="https://ark.cn-beijing.volces.com/api/v3",
        validation_alias=AliasChoices("doubao_base_url", "DOUBAO_BASE_URL"),
        description="火山引擎 Ark base URL；本身 OpenAI 兼容，直接走 openai SDK",
    )
    doubao_model: str = Field(
        default="doubao-seed-2-0-pro-260215",
        validation_alias=AliasChoices("doubao_model", "DOUBAO_MODEL"),
        description="豆包模型 ID 或 endpoint ID（ep-xxx）；vision-pro 默认值见此",
    )

    # Phase 10 多模态消息上限（同时约束 web /chat 和 wechat 入队）
    multimodal_max_images_per_turn: int = Field(
        default=4,
        ge=1,
        le=10,
        validation_alias=AliasChoices(
            "multimodal_max_images_per_turn", "SANSHILIU_MULTIMODAL_MAX_IMAGES",
        ),
        description="单轮请求最多接受多少张图；超过返回 400",
    )
    multimodal_max_image_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=64 * 1024,
        le=20 * 1024 * 1024,
        validation_alias=AliasChoices(
            "multimodal_max_image_bytes", "SANSHILIU_MULTIMODAL_MAX_IMAGE_BYTES",
        ),
        description="单张图解码后字节上限；默认 5MB",
    )
    wechat_merge_window_ms: int = Field(
        default=0,
        ge=0,
        le=60_000,
        validation_alias=AliasChoices(
            "wechat_merge_window_ms", "SANSHILIU_WECHAT_MERGE_WINDOW_MS",
        ),
        description=(
            "wechat 静默合并窗口（默认 0 = 不等）；适用于纯文本、图文齐备、视频/文件等"
            "完整 batch——下个 poll 周期立即触发 LLM。"
            "只有【图已到但未配文字】的特殊情况走 wechat_merge_window_media_ms。"
        ),
    )
    wechat_merge_window_media_ms: int = Field(
        default=5_000,
        ge=0,
        le=120_000,
        validation_alias=AliasChoices(
            "wechat_merge_window_media_ms", "SANSHILIU_WECHAT_MERGE_WINDOW_MEDIA_MS",
        ),
        description=(
            "wechat 图未配文等候窗口；用户发完图常会跟一句【这是什么】，5s 内文字到了立即合并发送，"
            "5s 后没文字就单图独发。"
        ),
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
    skills_dir_global: Path = Field(
        # 默认值是占位（仅当 env 未显式提供时由 _default_skills_dir_global 改成 home_dir/skills）；
        # 用户级全局 skills 目录（跨项目共享），优先级介于 project 与 repo 之间。
        default=Path("~/.sanshiliu/skills"),
        validation_alias=AliasChoices("skills_dir_global", "SANSHILIU_SKILLS_DIR_GLOBAL"),
        description="用户级全局 skills 目录（跨项目共享；优先级 project>global>repo）；缺省随 home_dir 走 <home_dir>/skills",
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

    # 做梦 scheduler（与 skills/dream/SKILL.md 配套；只在 serve 模式生效——REPL 进程不长跑）
    dream_scheduler_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "dream_scheduler_enabled", "SANSHILIU_DREAM_SCHEDULER_ENABLED"
        ),
        description="是否每天定时检查做梦闸门；只在 serve 模式跑后台 task",
    )
    dream_scheduler_hour: int = Field(
        default=3,
        ge=0,
        le=23,
        validation_alias=AliasChoices(
            "dream_scheduler_hour", "SANSHILIU_DREAM_SCHEDULER_HOUR"
        ),
        description="定时器醒来的小时（local time，0-23）；默认夜里 3 点",
    )
    dream_scheduler_min_sessions: int = Field(
        default=3,
        ge=1,
        validation_alias=AliasChoices(
            "dream_scheduler_min_sessions", "SANSHILIU_DREAM_SCHEDULER_MIN_SESSIONS"
        ),
        description="自上次做梦以来需累积多少个新 session 才放行；用户原话：对话文件 >= N 个",
    )

    # 成长 scheduler（与 skills/growth/SKILL.md 配套；只在 serve 模式生效——REPL 进程不长跑）
    # 数字分身从 start_age 起每天做一次"成长梦"、每梦跨 years_per_chapter 年，逻辑自洽承接前文，
    # 跑满 end_age 定格。growth_enabled 同时是 #5 外部 skill 自动安装的全局 kill-switch。
    growth_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("growth_enabled", "SANSHILIU_GROWTH_ENABLED"),
        description="是否启用成长系统；关=全局停（也是自动装 skill 的总开关）；只在 serve 模式跑",
    )
    growth_hour: int = Field(
        default=3,
        ge=0,
        le=23,
        validation_alias=AliasChoices("growth_hour", "SANSHILIU_GROWTH_HOUR"),
        description="成长定时器醒来的小时（local time，0-23）；默认夜里 3 点",
    )
    growth_years_per_chapter: int = Field(
        default=1,
        ge=1,
        validation_alias=AliasChoices(
            "growth_years_per_chapter", "SANSHILIU_GROWTH_YEARS_PER_CHAPTER"
        ),
        description="每章成长梦跨多少年；默认 1 年/章（5→30 岁共 25 章，每梦更具体）",
    )
    growth_start_age: int = Field(
        default=5,
        ge=0,
        validation_alias=AliasChoices("growth_start_age", "SANSHILIU_GROWTH_START_AGE"),
        description="成长起点年龄；默认 5 岁（原三十六贱笑起点）",
    )
    growth_end_age: int = Field(
        default=30,
        ge=1,
        validation_alias=AliasChoices("growth_end_age", "SANSHILIU_GROWTH_END_AGE"),
        description="成长终点年龄；跑满即定格，不再推进；默认 30 岁",
    )
    growth_birth_year: int = Field(
        default=1992,
        ge=1900,
        le=2100,
        validation_alias=AliasChoices("growth_birth_year", "SANSHILIU_GROWTH_BIRTH_YEAR"),
        description="成长起点对应的出生年（年龄 0 = 该公历年）；让每章算出公历年代，"
        "写实经历据此对应现实年代。默认 1992（即 1 岁≈1993）",
    )

    # 成长 phase-2 自动装 skill 配置（仅在 growth_enabled=true 时有意义；总 kill-switch 仍是 growth_enabled）。
    # phase-2 是 best-effort 安装：传记/状态已在 phase-1 推进，这里失败/超时绝不回退已成立的章。
    skill_install_timeout_sec: int = Field(
        default=60,
        ge=5,
        le=600,
        validation_alias=AliasChoices(
            "skill_install_timeout_sec", "SANSHILIU_SKILL_INSTALL_TIMEOUT_SEC"
        ),
        description="成长 phase-2 单次装 skill 的 bash 硬超时（秒）；防 npx 冷拉/无 TTY 挂死",
    )
    skill_install_prewarm: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "skill_install_prewarm", "SANSHILIU_SKILL_INSTALL_PREWARM"
        ),
        description="serve 启动是否预热 npx（暖 ~/.npm/_npx 缓存）；消除 3am 成长冷拉延迟/确认阻塞",
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

    @field_validator(
        "openai_base_url", "doubao_base_url", "ilink_base_url", "weixin_base_url",
        mode="after",
    )
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        """base_url 末尾不带 /，与 openai SDK 内部拼接一致，避免双斜杠 404。"""
        return v.rstrip("/")

    @model_validator(mode="after")
    def _default_skills_dir_global(self) -> Settings:
        """全局 skills 目录缺省随 home_dir 走 <home_dir>/skills，并 expanduser/resolve/mkdir。

        为什么用 model_validator 而非 field default_factory：要"跟随自定义 SANSHILIU_HOME_DIR"，
        必须在 home_dir 解析完之后再派生；只有用户没用 env 显式指定 skills_dir_global 时才覆盖，
        显式给了就尊重。建目录是因为 SkillLoader 扫描前要保证目录存在（不存在 iterdir 会出错）。

        为什么把"空串/纯空白"也当未设：`SANSHILIU_SKILLS_DIR_GLOBAL=`（空值）会让 model_fields_set
        判定为"已设"，但 pydantic 把空串/纯空白 coerce 成 Path("")，其 str() == "."（CWD 占位），
        .resolve() 会塌成 CWD——既不是用户本意、又跟 installer 脚本（它对空串走 falsy 兜底到
        <home>/skills）分道扬镳，两边落点不一致就是 bug #3。注意这里不能用 `not str(...).strip()` 判空：
        Path("") 的 str 是 "."（非空），漏判；故直接拿 str 比 "" / "." 这两个空/CWD 占位形态。
        （对"全局 skills 目录"而言，落到 CWD 从无合理语义，把显式 "." 也一并回落是预期行为。）
        """
        raw = str(self.skills_dir_global).strip()
        if "skills_dir_global" not in self.model_fields_set or raw in ("", "."):
            # 未显式提供 / 空白 / CWD 占位 → 派生为 <home_dir>/skills（home_dir 已被 field_validator 解析为绝对路径）
            self.skills_dir_global = self.home_dir / "skills"
        self.skills_dir_global = self.skills_dir_global.expanduser().resolve()
        self.skills_dir_global.mkdir(parents=True, exist_ok=True)
        return self

    @model_validator(mode="after")
    def _check_channel_dependencies(self) -> Settings:
        """通道启用时校验对应凭据，失败信息明确字段名（验收 1-V2 同款）。"""
        if self.wechat_enabled:
            official_ready = bool(self.weixin_account_id.strip() and self.weixin_token)
            webhook_ready = bool(self.ilink_api_key and self.ilink_webhook_secret)
            if not official_ready and not webhook_ready:
                raise ValueError(
                    "wechat_enabled=true 但缺少凭据：请配置 WEIXIN_ACCOUNT_ID + WEIXIN_TOKEN，"
                    "或配置 ILINK_API_KEY + ILINK_WEBHOOK_SECRET；"
                    "也可以运行 `python -m sanshiliu setup` 扫码，"
                    "或将 SANSHILIU_WECHAT_ENABLED 设为 false"
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局单例；首次加载并校验，测试可调用 cache_clear 重置。

    单一真相源：把解析后的 home_dir / skills_dir_global **回写进 os.environ**。独立的
    skill-installer 脚本（install-skill-from-github.py）只读 os.environ、不读 .env，又无 pydantic 派生，
    若不回写，它的落点会和 loader 扫的 skills_dir_global 分道扬镳——用户只在 .env 配 home，或把
    SANSHILIU_SKILLS_DIR_GLOBAL 设成空串时尤其明显（bug #1/#3：装一处、扫另一处，growth diff 永净 0）。
    回写后 installer 的 os.environ.get("SANSHILIU_SKILLS_DIR_GLOBAL") 一定拿到 loader 认定的那条绝对路径。

    用直接赋值（非 setdefault）：必须覆盖掉旧的/空的 env 值——否则空串/陈旧值仍会赢。get_settings 有
    lru_cache，整个进程只跑一次、幂等。只动这两个 SANSHILIU_* 键、且都设成它们解析后的真值，对其他
    子进程是良性的（dream/日常 bash 继承到的也只是这两条权威路径，不污染别的）。
    """
    try:
        settings = Settings()  # type: ignore[call-arg]  # pydantic-settings 运行时填充
    except Exception:
        # 保留 pydantic 原始字段错误信息
        logging.getLogger(__name__).exception("配置加载失败")
        raise
    os.environ["SANSHILIU_HOME_DIR"] = str(settings.home_dir)
    os.environ["SANSHILIU_SKILLS_DIR_GLOBAL"] = str(settings.skills_dir_global)
    return settings

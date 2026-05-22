"""prompts/*.md 加载器；compact/microcompact 的 LLM 指令外置存放，本模块只做读取。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sanshiliu.foundation.errors import ConfigError

# 系统级 prompt md 文件名；不在代码里塞任何字面 prompt 文本
COMPACT_FILE = "compact.md"
MICROCOMPACT_FILE = "microcompact.md"


@dataclass
class CompactPrompts:
    """缓存 prompts 目录中的指令文本；启动期一次性读，运行期不重读。"""

    compact_instruction: str
    microcompact_instruction: str
    prompts_dir: Path


def load_compact_prompts(prompts_dir: Path) -> CompactPrompts:
    """读取 prompts/*.md；缺文件抛 ConfigError 含字段名。"""
    missing: list[str] = []
    contents: dict[str, str] = {}

    for name in (COMPACT_FILE, MICROCOMPACT_FILE):
        path = prompts_dir / name
        if not path.is_file():
            missing.append(name)
            continue
        contents[name] = path.read_text(encoding="utf-8").strip()

    if missing:
        raise ConfigError(
            f"prompts 目录缺少必需的 md 文件：{', '.join(missing)}\n"
            f"  搜索目录：{prompts_dir}\n"
            "  解决：切到含 prompts/ 的工作目录，或设环境变量 SANSHILIU_PROMPTS_DIR=/path/to/prompts",
        )

    return CompactPrompts(
        compact_instruction=contents[COMPACT_FILE],
        microcompact_instruction=contents[MICROCOMPACT_FILE],
        prompts_dir=prompts_dir,
    )

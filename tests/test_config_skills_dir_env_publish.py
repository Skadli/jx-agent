"""单一真相源回归：get_settings 把解析后的 skills_dir_global 回写 os.environ，让独立 installer == loader。

复现的真实 bug（commit 4c767c4 之后用户实测到的 3 个，本测覆盖 #1/#3）：
- #1 .env-only 配置 installer 看不见：用户只在 .env 配 SANSHILIU_HOME_DIR/SANSHILIU_SKILLS_DIR_GLOBAL，
  Settings 走 pydantic 读 .env、能跟随；但独立 skill-installer 脚本只读 os.environ、不读 .env、无 pydantic
  派生 → 落点和 loader 扫的 skills_dir_global 分道扬镳 → growth 目录 diff 永净 0（全局目录改本想根治的失败）。
- #3 空串分歧：SANSHILIU_SKILLS_DIR_GLOBAL=（空）在 pydantic 里算"已设"，Path("").resolve()=CWD；但 installer
  对空串走 falsy 兜底到 <home>/skills → 两边落点不一致。

根治：get_settings 把 settings.home_dir / settings.skills_dir_global 直接回写 os.environ（覆盖而非 setdefault），
installer 的 _default_dest() 读这两条权威路径即与 loader 一致；并把 Settings 端空串也回落 <home>/skills。

测试隔离要点：get_settings 有 lru_cache 且现在会改 os.environ——每个用例都 cache_clear() + 用 monkeypatch
管控 env/CWD（自动复原），杜绝跨用例泄漏。Settings 的 env_file=".env" 是相对 CWD 解析，故 .env-only 用例
chdir 到 tmp_path 写隔离 .env，避开仓库真 .env。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from sanshiliu.foundation.config import get_settings

# 复用 test_skill_installer_dest 同款脚本路径定位（仓库根 → skills/skill-installer/scripts/...）
_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1] / "skills" / "skill-installer" / "scripts"
)
_SCRIPT_PATH = _SCRIPTS_DIR / "install-skill-from-github.py"


def _load_installer() -> ModuleType:
    """按路径加载带连字符名的 installer 脚本；scripts 目录入 sys.path 让其 import github_utils 成功。

    注意：脚本的 _default_dest() 每次调用都现读 os.environ（不缓存），故模块可一次加载、反复调用。
    """
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("install_skill_from_github", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolate_settings_cache() -> Iterator[None]:
    """每个用例前后都清 get_settings 缓存 + 还原它回写的两条 os.environ 键，杜绝跨用例泄漏。

    get_settings 现在会**直接赋值**回写 SANSHILIU_HOME_DIR / SANSHILIU_SKILLS_DIR_GLOBAL。
    光靠用例里的 monkeypatch 不足以兜住：当用例只 `delenv` 了某条（原本就不存在）时，
    monkeypatch 记下的是"原本缺失"，其 undo 对"测试期间被 get_settings 新塞进去的值"是 no-op，
    那条已发布的路径会残留到后续用例/会话（指向已删的 tmp 目录）。故这里在用例外层快照这两条键、
    finally 逐键还原（原本无则删、原本有则复原），让本文件无论用例怎么写都 leak-free。
    """
    _published_keys = ("SANSHILIU_HOME_DIR", "SANSHILIU_SKILLS_DIR_GLOBAL")
    saved = {k: os.environ.get(k) for k in _published_keys}
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def _write_min_env(env_path: Path, body: str) -> None:
    """写一份最小可加载的 .env：OPENAI_API_KEY 必填（缺则 Settings 启动失败），其余按 body 追加。"""
    env_path.write_text("OPENAI_API_KEY=sk-test-isolated\n" + body, encoding="utf-8")


def test_dotenv_only_home_makes_installer_match_loader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#1：只在 .env 配 SANSHILIU_HOME_DIR（无真实 env）→ 回写后 os.environ 与 settings 一致，
    且 installer 的 _default_dest() 解析出和 loader 的 skills_dir_global 同一条路径。
    """
    # 清掉可能从外部继承的两条 key，确保"只有 .env 提供配置"这一前提成立
    monkeypatch.delenv("SANSHILIU_HOME_DIR", raising=False)
    monkeypatch.delenv("SANSHILIU_SKILLS_DIR_GLOBAL", raising=False)
    # chdir 到 tmp_path 并写隔离 .env（避开仓库真 .env；env_file=".env" 相对 CWD 解析）
    custom_home = tmp_path / "custom-home"
    monkeypatch.chdir(tmp_path)
    _write_min_env(tmp_path / ".env", f"SANSHILIU_HOME_DIR={custom_home}\n")

    settings = get_settings()

    # skills_dir_global 跟随 .env 里的 home → <custom-home>/skills
    expected = (custom_home / "skills").resolve()
    assert settings.skills_dir_global == expected
    # 关键①：get_settings 把解析值回写进 os.environ（installer 只读这里）
    assert os.environ["SANSHILIU_SKILLS_DIR_GLOBAL"] == str(settings.skills_dir_global)
    assert os.environ["SANSHILIU_HOME_DIR"] == str(settings.home_dir)
    # 关键②：installer 的独立落点解析与 loader 完全一致（不再分道扬镳）
    installer = _load_installer()
    assert Path(installer._default_dest()) == settings.skills_dir_global


def test_dotenv_only_global_makes_installer_match_loader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#1 变体：只在 .env 显式配 SANSHILIU_SKILLS_DIR_GLOBAL（无真实 env）→ installer 同样跟到它。"""
    monkeypatch.delenv("SANSHILIU_HOME_DIR", raising=False)
    monkeypatch.delenv("SANSHILIU_SKILLS_DIR_GLOBAL", raising=False)
    twin_skills = tmp_path / "twin" / "skills"
    monkeypatch.chdir(tmp_path)
    _write_min_env(tmp_path / ".env", f"SANSHILIU_SKILLS_DIR_GLOBAL={twin_skills}\n")

    settings = get_settings()

    assert settings.skills_dir_global == twin_skills.resolve()
    assert os.environ["SANSHILIU_SKILLS_DIR_GLOBAL"] == str(settings.skills_dir_global)
    installer = _load_installer()
    assert Path(installer._default_dest()) == settings.skills_dir_global


def test_empty_global_env_falls_back_to_home_skills_not_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#3：SANSHILIU_SKILLS_DIR_GLOBAL=""（空）→ Settings 端回落 <home>/skills（绝非 CWD），
    回写后 installer 也解析到同一条（两边对空串达成一致，不再分歧）。
    """
    custom_home = tmp_path / "home3"
    # 用真实环境变量设空串（最贴近用户 `SANSHILIU_SKILLS_DIR_GLOBAL=` 的真实路径）
    monkeypatch.setenv("SANSHILIU_HOME_DIR", str(custom_home))
    monkeypatch.setenv("SANSHILIU_SKILLS_DIR_GLOBAL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-empty")
    # chdir 到一个明确不同于期望落点的目录，证明空串没有塌成 CWD
    work = tmp_path / "workdir"
    work.mkdir()
    monkeypatch.chdir(work)

    settings = get_settings()

    expected = (custom_home / "skills").resolve()
    assert settings.skills_dir_global == expected
    # 绝不是 CWD（旧 bug：Path("").resolve() == CWD）
    assert settings.skills_dir_global != work.resolve()
    # 回写后 installer 与 loader 一致（installer 对空串本就走 home 兜底，回写让两边都指向同一条）
    assert os.environ["SANSHILIU_SKILLS_DIR_GLOBAL"] == str(expected)
    installer = _load_installer()
    assert Path(installer._default_dest()) == settings.skills_dir_global


def test_env_publish_overrides_stale_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """回写用直接赋值而非 setdefault：os.environ 里预存的陈旧 SKILLS_DIR_GLOBAL 必须被解析值覆盖。

    保证 installer 永远读到 loader 认定的那条，而不是某次残留的旧路径（否则又分道扬镳）。
    """
    custom_home = tmp_path / "home-stale"
    monkeypatch.setenv("SANSHILIU_HOME_DIR", str(custom_home))
    # 预置一条与期望不同的陈旧值；若用 setdefault 就不会被覆盖（会留下 bug）
    monkeypatch.setenv("SANSHILIU_SKILLS_DIR_GLOBAL", str(tmp_path / "STALE" / "skills"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-stale")

    settings = get_settings()

    # 显式给了非空 GLOBAL → 尊重它（这里测的是"回写=解析值"，故断言 os.environ 等于解析后的 settings 值）
    assert os.environ["SANSHILIU_SKILLS_DIR_GLOBAL"] == str(settings.skills_dir_global)
    installer = _load_installer()
    assert Path(installer._default_dest()) == settings.skills_dir_global

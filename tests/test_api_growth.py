"""dashboard 成长读端点（PR4）单测；覆盖三个 handler 的正常路径 + 边界 + 遍历防护。

无真 HTTP server：用最小 FakeReq 桩捕获 status + JSON body（handler 只用 path / send_response /
send_header / end_headers / wfile.write）。状态/人格/传记全在 tmp_path 下造文件，mock 路径即可，
不起整条 serve 链路。

被测：
- /api/growth：有 state 返回总览 JSON；无 state（成长未激活）回空闲态、不报 500。
- /api/growth/chapters/{n}：合法 n 返回章详情 + 传记；越界 404；非法 n（含 ..）400。
- /api/growth/persona/{n}：读 data/growth/persona/chapter-n/ 下 md；拒 `..` 遍历；缺目录 404。
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from sanshiliu.channels.web.api_growth import (
    make_growth_chapter_handler,
    make_growth_overview_handler,
    make_growth_persona_handler,
)
from sanshiliu.channels.web.api_writes import make_growth_chapter_delete_handler
from sanshiliu.memory.longterm.memdir import write_memory_file
from sanshiliu.memory.types import MemoryEntry
from sanshiliu.scheduler.growth_persona import chapter_persona_dir
from sanshiliu.scheduler.growth_state import (
    ChapterRecord,
    GrowthState,
    load_growth_state,
    save_growth_state,
)


class FakeReq(BaseHTTPRequestHandler):
    """最小 BaseHTTPRequestHandler 桩：记录 status，把写出的 body 攒进 wfile（BytesIO）。

    继承 BaseHTTPRequestHandler 是为了**类型相容**（各 handler 形参标注的就是它）；不调
    super().__init__（那要 socket），只覆盖 handler 实际会用到的几个方法/属性。
    """

    def __init__(self, path: str) -> None:  # 不调 super().__init__（无需 socket）
        self.path = path
        self.status = 0
        # base 把 wfile 标成 BufferedIOBase（无 getvalue）；另存一个 BytesIO 引用供 json() 读回
        self._buf = io.BytesIO()
        self.wfile = self._buf

    def send_response(self, code: int, message: str | None = None) -> None:
        self.status = code

    def send_header(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def end_headers(self) -> None:
        pass

    def json(self) -> Any:
        return json.loads(self._buf.getvalue().decode("utf-8"))


def _state_with_two_chapters() -> GrowthState:
    state = GrowthState()
    state.advance(
        ChapterRecord(
            age_range="5-6",
            summary="从三十六贱笑长成爱写段子的小学生。",
            report="主人，我这一年长成了贫嘴的小学生。",
            installed_skills=["standup-comedy"],
        )
    )
    state.advance(
        ChapterRecord(
            age_range="6-7",
            summary="接住上一章的段子魂，成了校园博主。",
            report="主人，我现在是校园博主了。",
            installed_skills=["standup-comedy", "video-editing"],
        )
    )
    return state


# ── /api/growth 总览 ──────────────────────────────────────────────────


def test_overview_returns_state(tmp_path: Path) -> None:
    state_path = tmp_path / "growth-state.json"
    save_growth_state(state_path, _state_with_two_chapters())
    handler = make_growth_overview_handler(
        state_path, start_age=5, years_per_chapter=1, end_age=30, enabled=True
    )
    req = FakeReq("/api/growth")
    handler(req)

    assert req.status == 200
    body = req.json()
    assert body["enabled"] is True
    assert body["current_chapter"] == 2
    assert body["age"] == 7  # 1 年/章：5 + 2*1
    assert body["timeline"] == {"start_age": 5, "end_age": 30, "current_age": 7}
    # installed_skills 跨章去重汇总
    assert body["installed_skills"] == ["standup-comedy", "video-editing"]
    assert len(body["chapters"]) == 2
    assert body["chapters"][0]["chapter_no"] == 1
    assert body["chapters"][0]["report"] == "主人，我这一年长成了贫嘴的小学生。"
    assert body["chapters"][0]["persona_snapshot_ref"] == "/api/growth/persona/1"


def test_overview_idle_state_when_no_file(tmp_path: Path) -> None:
    # 成长未激活（无 state 文件）→ 回空闲态、200、不报 500
    handler = make_growth_overview_handler(
        tmp_path / "missing.json", start_age=5, years_per_chapter=1, end_age=30, enabled=False
    )
    req = FakeReq("/api/growth")
    handler(req)

    assert req.status == 200
    body = req.json()
    assert body["enabled"] is False
    assert body["current_chapter"] == 0
    assert body["chapters"] == []
    assert body["installed_skills"] == []
    assert body["frozen"] is False


# ── /api/growth/chapters/{n} ──────────────────────────────────────────


def test_chapter_detail_valid(tmp_path: Path) -> None:
    state_path = tmp_path / "growth-state.json"
    save_growth_state(state_path, _state_with_two_chapters())
    memdir = tmp_path / "memdir"
    # 写一份第 1 章传记（write_memory_file 文件名带时间戳，handler 按前缀 glob）
    write_memory_file(
        memdir,
        MemoryEntry(
            name="growth-chapter-1",
            description="成长传记 第1章 - 5-10岁",
            memory_type="reference",
            source="growth-chapter-1",
        ),
        body="第一章传记正文：小学生段子手。",
    )
    handler = make_growth_chapter_handler(
        state_path, memdir, start_age=5, years_per_chapter=1, end_age=30
    )
    req = FakeReq("/api/growth/chapters/1")
    handler(req)

    assert req.status == 200
    body = req.json()
    assert body["chapter_no"] == 1
    assert body["age_range"] == "5-6"
    assert body["report"] == "主人，我这一年长成了贫嘴的小学生。"
    assert "小学生段子手" in body["biography"]
    assert body["installed_skills"] == ["standup-comedy"]


def test_chapter_detail_out_of_range_404(tmp_path: Path) -> None:
    state_path = tmp_path / "growth-state.json"
    save_growth_state(state_path, _state_with_two_chapters())
    handler = make_growth_chapter_handler(
        state_path, tmp_path / "memdir", start_age=5, years_per_chapter=1, end_age=30
    )
    # 只完成 2 章，第 3 章还没长到 → 404
    req = FakeReq("/api/growth/chapters/3")
    handler(req)
    assert req.status == 404


def test_chapter_detail_invalid_n_400(tmp_path: Path) -> None:
    state_path = tmp_path / "growth-state.json"
    save_growth_state(state_path, _state_with_two_chapters())
    handler = make_growth_chapter_handler(
        state_path, tmp_path / "memdir", start_age=5, years_per_chapter=1, end_age=30
    )
    for bad in ("/api/growth/chapters/abc", "/api/growth/chapters/0", "/api/growth/chapters/-1"):
        req = FakeReq(bad)
        handler(req)
        assert req.status == 400, bad


# ── DELETE /api/growth/chapters/{n} ───────────────────────────────────


def _delete_handler(
    state_path: Path, *, growth_running: Callable[[], bool] | None = None
) -> Any:  # 工厂来自未标 py.typed 的包 → 返回 Any；精确标注只会触发 no-any-return
    return make_growth_chapter_delete_handler(
        state_path,
        start_age=5,
        years_per_chapter=1,
        end_age=30,
        data_dir=state_path.parent,  # 测试里 state 文件与 data_dir 同在 tmp_path
        memdir_loader=None,
        persona_loader=None,
        growth_running=growth_running,
    )


def test_delete_chapter_one_clears_all_and_persists(tmp_path: Path) -> None:
    state_path = tmp_path / "growth-state.json"
    save_growth_state(state_path, _state_with_two_chapters())
    req = FakeReq("/api/growth/chapters/1")  # 删第 1 章 = 连带第 2 章一起清空
    _delete_handler(state_path)(req)

    assert req.status == 200
    body = req.json()
    assert body["ok"] is True
    assert body["removed_chapters"] == [1, 2]
    assert body["current_chapter"] == 0
    # 真落盘：重新 load 确认状态被截断（不是只改了内存）
    reloaded = load_growth_state(state_path)
    assert reloaded.current_chapter == 0
    assert reloaded.chapters == []


def test_delete_latest_chapter_rewinds_one(tmp_path: Path) -> None:
    state_path = tmp_path / "growth-state.json"
    save_growth_state(state_path, _state_with_two_chapters())
    req = FakeReq("/api/growth/chapters/2")  # 只删最新一章
    _delete_handler(state_path)(req)

    assert req.status == 200
    assert req.json()["removed_chapters"] == [2]
    reloaded = load_growth_state(state_path)
    assert reloaded.current_chapter == 1
    assert reloaded.age == 6  # 1 年/章：5 + 1*1
    assert reloaded.active_persona_chapter == 1


def test_delete_out_of_range_404_leaves_state_untouched(tmp_path: Path) -> None:
    state_path = tmp_path / "growth-state.json"
    save_growth_state(state_path, _state_with_two_chapters())
    req = FakeReq("/api/growth/chapters/3")  # 只完成 2 章
    _delete_handler(state_path)(req)
    assert req.status == 404
    assert load_growth_state(state_path).current_chapter == 2  # 未改


def test_delete_invalid_n_400(tmp_path: Path) -> None:
    state_path = tmp_path / "growth-state.json"
    save_growth_state(state_path, _state_with_two_chapters())
    for bad in (
        "/api/growth/chapters/abc",
        "/api/growth/chapters/0",
        "/api/growth/chapters/-1",
        "/api/growth/chapters/..",
    ):
        req = FakeReq(bad)
        _delete_handler(state_path)(req)
        assert req.status == 400, bad
    assert load_growth_state(state_path).current_chapter == 2  # 非法请求绝不改状态


def test_delete_all_reseeds_cadence_from_config(tmp_path: Path) -> None:
    # #1：旧状态是 5 年/章（end_chapter=5）。清空全部后须按当前 config（1 年/章）重新 seed，
    # 否则文件作为真相源会一直粘着旧 cadence，与新默认/文档（1 年/25 章）不一致。
    state_path = tmp_path / "growth-state.json"
    old = GrowthState(years_per_chapter=5, end_chapter=5)
    old.advance(ChapterRecord(age_range="5-10", summary="旧章1"))
    old.advance(ChapterRecord(age_range="10-15", summary="旧章2"))
    save_growth_state(state_path, old)

    req = FakeReq("/api/growth/chapters/1")  # 清空全部（handler 配的是 1 年/章、30 岁）
    _delete_handler(state_path)(req)
    assert req.status == 200

    reloaded = load_growth_state(state_path)
    assert reloaded.current_chapter == 0
    assert reloaded.years_per_chapter == 1  # 已迁到新 cadence
    assert reloaded.end_chapter == 25
    assert reloaded.age == 5


def test_delete_removes_persona_snapshot_dirs(tmp_path: Path) -> None:
    # #2：删章要连人格快照目录 data/growth/persona/chapter-N/ 一起删（chapter-0 起点保留）。
    state_path = tmp_path / "growth-state.json"
    save_growth_state(state_path, _state_with_two_chapters())
    data_dir = state_path.parent  # _delete_handler 用 state_path.parent 当 data_dir
    for n in (0, 1, 2):
        d = chapter_persona_dir(data_dir, n)
        d.mkdir(parents=True, exist_ok=True)
        (d / "identity.md").write_text("人格", encoding="utf-8")

    req = FakeReq("/api/growth/chapters/2")  # 只删第 2 章
    _delete_handler(state_path)(req)
    assert req.status == 200
    assert req.json()["deleted_persona_dirs"] == 1

    assert chapter_persona_dir(data_dir, 0).is_dir()  # 起点快照保留
    assert chapter_persona_dir(data_dir, 1).is_dir()  # 未删的章保留
    assert not chapter_persona_dir(data_dir, 2).is_dir()  # 被删章的人格目录已清


def test_delete_rejected_while_growth_running(tmp_path: Path) -> None:
    # #3：成长任务正在跑时拒删（409），且绝不改状态——避免与 GrowthRunner 抢同一状态文件。
    state_path = tmp_path / "growth-state.json"
    save_growth_state(state_path, _state_with_two_chapters())
    req = FakeReq("/api/growth/chapters/1")
    _delete_handler(state_path, growth_running=lambda: True)(req)
    assert req.status == 409
    assert load_growth_state(state_path).current_chapter == 2  # 状态未动


# ── /api/growth/persona/{n} ───────────────────────────────────────────


def test_persona_snapshot_reads_chapter_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    ch1 = chapter_persona_dir(data_dir, 1)
    ch1.mkdir(parents=True, exist_ok=True)
    (ch1 / "identity.md").write_text("我现在是校园博主。", encoding="utf-8")
    (ch1 / "style.md").write_text("口语、爱玩梗。", encoding="utf-8")

    handler = make_growth_persona_handler(data_dir)
    req = FakeReq("/api/growth/persona/1")
    handler(req)

    assert req.status == 200
    body = req.json()
    assert body["chapter_no"] == 1
    names = {f["name"] for f in body["files"]}
    assert names == {"identity.md", "style.md"}
    by_section = {f["section"]: f["body"] for f in body["files"]}
    assert by_section["identity"] == "我现在是校园博主。"
    assert by_section["style"] == "口语、爱玩梗。"


def test_persona_snapshot_missing_dir_404(tmp_path: Path) -> None:
    handler = make_growth_persona_handler(tmp_path / "data")
    req = FakeReq("/api/growth/persona/7")  # 该章人格目录不存在
    handler(req)
    assert req.status == 404


def test_persona_snapshot_rejects_traversal(tmp_path: Path) -> None:
    # 路径里塞 .. / 非数字段：_parse_chapter_no 形状校验直接拒，绝不读到成长根之外。
    # 末两个是 Unicode "数字"（上标 ²、阿拉伯-印度 ١）：str.isdigit() 对它们回 True，
    # 但 int() 会抛 ValueError / 解析成意外值——必须当 400 拒掉而非冒成 500。
    handler = make_growth_persona_handler(tmp_path / "data")
    for bad in (
        "/api/growth/persona/..",
        "/api/growth/persona/%2e%2e",
        "/api/growth/persona/1/../../etc",
        "/api/growth/persona/abc",
        "/api/growth/persona/²",
        "/api/growth/persona/١",
    ):
        req = FakeReq(bad)
        handler(req)
        assert req.status == 400, bad

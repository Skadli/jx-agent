"""命运种子卡池：只规范"方向"（世界类型）+ 创意度尺度，出身/天赋/触发由第1章大模型现写。

12 类世界（修仙/科幻/异世界/盗墓/克苏鲁/无限流/武侠/游戏/悬疑/末世 + 写实博主线/喜剧人生线）
是这张卡唯一的硬方向约束；GenreSpec.triggers 现在**仅供前端选择器展示该类型的氛围示例**，
不再抽进卡里——开头的出身、家庭、命运触发一律由 forge_runner 第1章的大模型原创。
draw_seed 只随机定方向 + 撒一颗发散种子（divergence）逼每张卡的开头不收敛成套路；
它是唯一抽取入口：API 与冒烟脚本都经它抽种子，保证字段形状一致，rng 可注入便于单测复现。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sanshiliu.foundation.logging import get_logger
from sanshiliu.gacha.card_state import CardSeed

_logger = get_logger(__name__)

# 抽卡未指定创意度时的随机区间（保守 0 ↔ 2 狂野；默认抽在中间偏上，让卡有戏）
_CREATIVITY_RANGE = (0.6, 1.6)


@dataclass(frozen=True)
class GenreSpec:
    """一类世界：id 供 API/卡面用，label 供展示；triggers 仅作选择器里的氛围示例
    （展示这类世界"可能长什么样"），**不再被抽进卡**——卡的命运触发由第1章大模型现写。"""

    id: str
    label: str
    icon: str
    triggers: tuple[str, ...]


GENRES: tuple[GenreSpec, ...] = (
    GenreSpec(
        "xiuxian",
        "修仙",
        "⚔️",
        (
            "捡到一枚刻着古文的戒指",
            "旧书摊偶得残页秘籍",
            "山里迷路误入洞府",
            "遇到一位看穿你根骨的高人",
            "雷雨夜被天雷劈中却毫发无伤",
            "高烧一场后血脉觉醒",
        ),
    ),
    GenreSpec(
        "scifi",
        "科幻",
        "🚀",
        (
            "收音机里收到规律的异常信号",
            "后山发现一块还温热的陨石",
            "无意撞破一项军方机密",
            "工地下挖出非人造物遗迹",
            "实验事故让纳米粒子入体",
            "深夜目击不该存在的生物",
        ),
    ),
    GenreSpec(
        "isekai",
        "异世界",
        "🌀",
        (
            "老宅穿衣镜里有另一个世界",
            "落水后在陌生大陆醒来",
            "一场反复出现的梦变成了门",
            "电梯坠落却落在异界草原",
            "隧道尽头的光不是出口",
        ),
    ),
    GenreSpec(
        "tomb",
        "盗墓",
        "🏺",
        (
            "祖屋夹墙里发现一张羊皮地图",
            "传家的罗盘在某个方位疯转",
            "读懂了先人笔记里的暗语",
            "收到一把不知开哪扇门的古钥匙",
            "拓碑时解出一段石碑密码",
        ),
    ),
    GenreSpec(
        "cthulhu",
        "克苏鲁",
        "🐙",
        (
            "翻开了不该翻开的禁书",
            "梦里反复念出不懂的语言",
            "短波电台收到深海的呼唤",
            "收到精神病院寄来的旧信",
            "老照片角落总有同一个符号",
        ),
    ),
    GenreSpec(
        "infinity",
        "无限流",
        "📱",
        (
            "手机里多了个删不掉的 APP",
            "凌晨收到坐标格式的短信",
            "邮箱里出现一封任务邮件",
            "扫了一张贴在电线杆上的二维码",
            "捡到一个自动挂载的 USB",
        ),
    ),
    GenreSpec(
        "martial",
        "武侠",
        "🥋",
        (
            "阁楼翻出一本古拳谱",
            "宗祠大火那夜血脉觉醒",
            "地动山摇中莫名开了筋脉",
            "祖传的旧兵器突然嗡鸣",
            "夜市黑摊上的一场奇遇",
        ),
    ),
    GenreSpec(
        "game",
        "游戏世界",
        "🎮",
        (
            "VR 头盔摘不下来了",
            "改一个 bug 时代码成了真",
            "探进一台废弃服务器的世界",
            "运动手环开始给你发任务",
            "屏幕里的角色把你拉了进去",
        ),
    ),
    GenreSpec(
        "mystery",
        "悬疑",
        "🕵️",
        (
            "收到一封落款是十年后的信",
            "老照片里多出一个人",
            "捡到一盘没有标签的录音带",
            "对门邻居一夜之间人间蒸发",
            "签收了一个无名快递",
        ),
    ),
    GenreSpec(
        "zombie",
        "末世",
        "🧟",
        (
            "新闻里的怪病越来越近",
            "隔壁实验室半夜拉响警报",
            "在旧货市场囤到一只急救包",
            "发现小区地下有处避难所",
            "对讲机里传来求救信号",
        ),
    ),
    GenreSpec(
        "blogger",
        "写实博主线",
        "📹",
        (
            "随手发的一条视频突然爆了",
            "被一家 MCN 的星探私信",
            "一场直播事故反而涨粉",
            "作品被大号抄袭引发骂战",
            "在菜市场被粉丝认出",
        ),
    ),
    GenreSpec(
        "comedy",
        "喜剧人生线",
        "🎭",
        (
            "开放麦首秀炸了全场",
            "段子被大 V 转发一夜十万赞",
            "拜了一位说书老艺人为师",
            "比赛爆冷干掉了夺冠热门",
            "演出散场后被星探拦住",
        ),
    ),
)


def find_genre(genre_id: str) -> GenreSpec | None:
    for spec in GENRES:
        if spec.id == genre_id:
            return spec
    return None


def draw_seed(
    *,
    genre: str | None = None,
    custom_prompt: str = "",
    creativity: float | None = None,
    birth_year: int = 1992,
    rng: random.Random | None = None,
) -> CardSeed:
    """抽一颗命运种子：只定方向（genre）+ 尺度（creativity）+ 一颗发散种子（divergence）。

    genre 缺省 / "random" / 不认识 → 随机类型；creativity 缺省随机抽。出身 / 天赋 / 命运触发
    **不再在这里抽**——它们由 forge_runner 第1章的大模型现写后回填进 seed（卡面据此展示、
    后续各章常驻延续）。divergence 是撒给第1章的随机发散种子，逼每张卡的开头不收敛成套路。
    """
    r = rng if rng is not None else random.Random()
    spec = find_genre(genre) if genre and genre != "random" else None
    if genre and genre != "random" and spec is None:
        _logger.info("未知世界类型，按随机处理", genre=genre)
    if spec is None:
        spec = r.choice(GENRES)
    cre = creativity if creativity is not None else round(r.uniform(*_CREATIVITY_RANGE), 1)
    return CardSeed(
        genre=spec.id,
        genre_label=spec.label,
        creativity=min(max(cre, 0.0), 2.0),
        custom_prompt=custom_prompt.strip(),
        birth_year=birth_year,
        divergence=r.randrange(1000, 1_000_000),
    )

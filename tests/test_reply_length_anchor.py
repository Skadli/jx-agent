from sanshiliu.engine.session import Session


def _fake_persona(text: str) -> object:
    return type("P", (), {"to_system_prompt": lambda self: text})()


def test_anchor_is_last_section_of_system_prompt() -> None:
    sess = Session.new(channel="web")
    sess.refresh_system_prompt(_fake_persona("人设正文在这里"))
    sess.add_user("献血有什么好处")

    sys_text = sess.to_openai_messages()[0]["content"]

    # 锚点必须落在最末尾的近因位，且排在人设正文之后
    last_section = sys_text.split("\n\n---\n\n")[-1]
    assert "（发送前自检）" in last_section
    assert sys_text.index("人设正文在这里") < sys_text.index("（发送前自检）")


def test_anchor_present_even_without_persona() -> None:
    # 没有人设/记忆/skills 时，锚点仍要在（兜底：始终提醒别长别 markdown）
    sess = Session.new(channel="web")
    sys_text = sess.to_openai_messages()[0]["content"]
    assert "不用 markdown" in sys_text


def test_anchor_carries_no_hardcoded_char_count() -> None:
    # 锚点不复刻字数，避免和 style.md 的"≤60字"形成双源漂移
    sess = Session.new(channel="web")
    sys_text = sess.to_openai_messages()[0]["content"]
    anchor = sys_text.split("\n\n---\n\n")[-1]
    assert "60" not in anchor

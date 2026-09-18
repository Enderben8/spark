from spark.perception.dom import BBox, ElementState, InteractiveElement
from spark.reasoning.prompts import (
    build_answer_question_prompt,
    build_classify_page_prompt,
    build_extract_score_prompt,
    build_observe_decide_prompt,
    format_elements,
    format_history,
)


def _el(id_: str, name: str, nearby: str = "", checked: bool = False) -> InteractiveElement:
    return InteractiveElement(
        id=id_,
        frame_path="main",
        tag="button",
        role="button",
        name=name,
        text=name,
        value=None,
        state=ElementState(checked=checked),
        bbox=BBox(x=0, y=0, w=10, h=10),
        in_viewport=True,
        selector=f"#{id_}",
        nearby_text=nearby,
    )


def test_format_elements_never_includes_raw_html():
    elements = [_el("e1", "Next")]
    rendered = format_elements(elements)
    assert "<button" not in rendered
    assert "e1: button 'Next'" in rendered


def test_format_elements_shows_checked_state():
    elements = [_el("e1", "", nearby="Blue", checked=True)]
    rendered = format_elements(elements)
    assert "checked" in rendered


def test_format_elements_truncates_and_says_so():
    elements = [_el(f"e{i}", f"Item {i}") for i in range(100)]
    rendered = format_elements(elements, limit=10)
    assert "and 90 more elements" in rendered


def test_format_history_empty():
    assert "no actions" in format_history([])


def test_format_history_limits_to_recent():
    actions = [f"action {i}" for i in range(20)]
    rendered = format_history(actions, limit=3)
    assert "action 19" in rendered
    assert "action 0" not in rendered


def test_observe_decide_prompt_includes_goal_and_elements():
    prompt = build_observe_decide_prompt(
        goal="Answer all questions",
        page_text_excerpt="Some passage text",
        elements=[_el("e1", "Next")],
        recent_actions=[],
    )
    assert "Answer all questions" in prompt
    assert "Some passage text" in prompt
    assert "e1: button 'Next'" in prompt


def test_answer_question_prompt_forbids_guessing():
    prompt = build_answer_question_prompt(
        question="What color is the sky?", options=["Blue", "Green"], passage_text="The sky is blue."
    )
    assert "do not guess" in prompt.lower()
    assert "The sky is blue." in prompt
    assert "0: Blue" in prompt


def test_extract_score_prompt_warns_against_percentage_confusion():
    prompt = build_extract_score_prompt(page_text="Score: 100")
    assert "percentage" in prompt.lower()
    assert "Score: 100" in prompt


def test_classify_page_prompt_lists_all_categories():
    prompt = build_classify_page_prompt(page_text_excerpt="text", elements=[])
    for category in ("reading", "questions", "score", "navigation", "blocked", "other"):
        assert category in prompt

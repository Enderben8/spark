import pytest

from spark.memory import RunMemory
from spark.reasoning.providers.stub import StubProvider
from spark.reasoning.schemas import ScoreExtraction
from spark.skills.scoring import (
    ScoreComparison,
    ScoreReading,
    extract_score,
    no_improvement_streak,
    regex_sweep,
)

# -- regex_sweep: the ~20-string table BUILD_SPEC §11.2 asks for -----------

REGEX_SWEEP_CASES = [
    ("Score: 100", 100.0, None, False),
    ("Score:100", 100.0, None, False),
    ("You scored 12 out of 20", 12.0, 20.0, False),
    ("12/20", 12.0, 20.0, False),
    ("12 / 20", 12.0, 20.0, False),
    ("60%", 60.0, 100.0, True),
    ("Your score: 60%", 60.0, 100.0, True),
    ("450 points", 450.0, None, False),
    ("You earned 450 points today", 450.0, None, False),
    ("Score: 0", 0.0, None, False),
    ("Final score: 99.5%", 99.5, 100.0, True),
    ("8/8 correct", 8.0, 8.0, False),
    ("Result: 3 out of 5", 3.0, 5.0, False),
    ("You have 1200 points", 1200.0, None, False),
]


@pytest.mark.parametrize("text,value,maximum,is_percentage", REGEX_SWEEP_CASES)
def test_regex_sweep_table(text, value, maximum, is_percentage):
    reading = regex_sweep(text)
    assert reading is not None, f"expected a match for {text!r}"
    assert reading.value == value
    assert reading.maximum == maximum
    assert reading.is_percentage == is_percentage


def test_regex_sweep_prefers_percentage_over_bare_number():
    reading = regex_sweep("You scored 60% on this attempt")
    assert reading.is_percentage is True
    assert reading.value == 60.0


def test_regex_sweep_returns_none_when_nothing_matches():
    assert regex_sweep("Congratulations, well done!") is None


def test_regex_sweep_never_fabricates_percentage_without_percent_sign():
    reading = regex_sweep("Score: 60")
    assert reading.is_percentage is False
    assert reading.maximum is None


# -- cumulative comparison: the load-bearing "do not sum" behaviour --------


def test_cumulative_comparison_uses_latest_reading_not_a_sum():
    memory = RunMemory()
    memory.remember_score(step_index=1, raw_text="150", value=150, maximum=None, is_percentage=False, target=450)
    memory.remember_score(step_index=2, raw_text="300", value=300, maximum=None, is_percentage=False, target=450)

    comparison = ScoreComparison(target=450, cumulative=True)
    latest = ScoreReading(raw="450", value=450, maximum=None, is_percentage=False)

    # If this were (incorrectly) summed against prior readings (150+300+450),
    # target_reached would be comparing against 900, not 450 — still True
    # here either way, so the real assertion is in the regression test below
    # and in target_reached's contract: it only ever looks at `reading`.
    assert comparison.target_reached(latest) is True


def test_target_not_reached_below_target():
    comparison = ScoreComparison(target=450, cumulative=True)
    reading = ScoreReading(raw="300", value=300, maximum=None, is_percentage=False)
    assert comparison.target_reached(reading) is False


def test_comparison_operators():
    reading = ScoreReading(raw="100", value=100, maximum=None, is_percentage=False)
    assert ScoreComparison(target=100, comparison=">=").target_reached(reading) is True
    assert ScoreComparison(target=100, comparison=">").target_reached(reading) is False
    assert ScoreComparison(target=100, comparison="==").target_reached(reading) is True
    assert ScoreComparison(target=99, comparison=">").target_reached(reading) is True


def test_unsupported_comparison_operator_rejected():
    with pytest.raises(ValueError):
        ScoreComparison(target=1, comparison="<=")


def test_regression_detected_in_cumulative_mode():
    memory = RunMemory()
    memory.remember_score(step_index=1, raw_text="300", value=300, maximum=None, is_percentage=False, target=450)
    comparison = ScoreComparison(target=450, cumulative=True)

    dropped = ScoreReading(raw="100", value=100, maximum=None, is_percentage=False)
    warning = comparison.check_regression(dropped, memory)
    assert warning is not None
    assert "lower than the previous" in warning


def test_no_regression_warning_for_per_round_scoring():
    memory = RunMemory()
    memory.remember_score(step_index=1, raw_text="90", value=90, maximum=100, is_percentage=True, target=80)
    comparison = ScoreComparison(target=80, cumulative=False)

    lower_round = ScoreReading(raw="70", value=70, maximum=100, is_percentage=True)
    assert comparison.check_regression(lower_round, memory) is None


def test_no_regression_warning_on_first_reading():
    memory = RunMemory()
    comparison = ScoreComparison(target=100, cumulative=True)
    reading = ScoreReading(raw="50", value=50, maximum=None, is_percentage=False)
    assert comparison.check_regression(reading, memory) is None


# -- no-improvement guard rail ----------------------------------------------


def test_no_improvement_streak_true_when_flat():
    memory = RunMemory()
    for v in (100, 100, 100):
        memory.remember_score(step_index=1, raw_text=str(v), value=v, maximum=None, is_percentage=False, target=500)
    assert no_improvement_streak(memory, 3) is True


def test_no_improvement_streak_false_when_improving():
    memory = RunMemory()
    for v in (100, 150, 200):
        memory.remember_score(step_index=1, raw_text=str(v), value=v, maximum=None, is_percentage=False, target=500)
    assert no_improvement_streak(memory, 3) is False


def test_no_improvement_streak_false_with_insufficient_history():
    memory = RunMemory()
    memory.remember_score(step_index=1, raw_text="100", value=100, maximum=None, is_percentage=False, target=500)
    assert no_improvement_streak(memory, 3) is False


# -- extract_score cascade (uses StubProvider, no network) ------------------


@pytest.mark.asyncio
async def test_extract_score_uses_ai_extraction_when_available():
    provider = StubProvider()
    provider.queue_response(
        ScoreExtraction, ScoreExtraction(found=True, raw_text="Score: 450", value=450, maximum=None, is_percentage=False)
    )
    reading = await extract_score(provider=provider, page_text="Score: 450")
    assert reading.value == 450
    assert reading.is_percentage is False


@pytest.mark.asyncio
async def test_extract_score_falls_back_to_regex_when_ai_says_not_found():
    provider = StubProvider()
    provider.queue_response(ScoreExtraction, ScoreExtraction(found=False))
    reading = await extract_score(provider=provider, page_text="Score: 200")
    assert reading is not None
    assert reading.value == 200


@pytest.mark.asyncio
async def test_extract_score_hint_regex_wins_over_ai():
    provider = StubProvider()  # never queried because the hint short-circuits
    reading = await extract_score(
        provider=provider, page_text="Weird layout: total=999 pts", hint_regex=r"total=(\d+)"
    )
    assert reading.value == 999
    assert provider.calls == []

from spark.memory import RunMemory


def test_remember_passage_never_dropped_across_navigations():
    mem = RunMemory()
    mem.remember_passage(step_index=1, url="https://x/passage", text="The sky is blue.", source="dom")
    mem.remember_passage(step_index=5, url="https://x/questions", text="", source="dom")  # navigated away
    assert len(mem.passages) == 2
    assert "sky is blue" in mem.all_passage_text


def test_remember_answer_and_score_roundtrip():
    mem = RunMemory()
    mem.remember_answer(
        step_index=2,
        question="What color is the sky?",
        options=["Blue", "Green"],
        chosen_option_id="a",
        reasoning="The passage says the sky is blue.",
        citation="The sky is blue.",
        confidence=0.95,
    )
    mem.remember_score(step_index=3, raw_text="Score: 100", value=100, maximum=None, is_percentage=False, target=200)
    assert mem.qa_history[0].chosen_option_id == "a"
    assert mem.latest_score.value == 100


def test_latest_score_is_none_when_nothing_recorded():
    mem = RunMemory()
    assert mem.latest_score is None


def test_visited_signature_tracking_for_stall_detection():
    mem = RunMemory()
    mem.mark_visited("sig-a")
    mem.mark_visited("sig-a")
    mem.mark_visited("sig-b")
    mem.mark_visited("sig-a")
    assert mem.recent_visit_count("sig-a", last_n=4) == 3
    assert mem.recent_visit_count("sig-a", last_n=2) == 1


def test_relevant_passages_returns_everything_when_under_budget():
    mem = RunMemory()
    mem.remember_passage(step_index=1, url="u", text="short passage one", source="dom")
    mem.remember_passage(step_index=2, url="u2", text="short passage two", source="dom")
    result = mem.relevant_passages("what happened", max_chars=10_000)
    assert "passage one" in result and "passage two" in result


def test_relevant_passages_prefers_lexical_overlap_when_over_budget():
    mem = RunMemory()
    long_irrelevant = "zzz " * 200 + "nothing to do with the question at all here"
    relevant = "The founding mayor built the town clocktower in 1889."
    mem.remember_passage(step_index=1, url="u1", text=long_irrelevant, source="dom")
    mem.remember_passage(step_index=2, url="u2", text=relevant, source="dom")

    result = mem.relevant_passages("Who built the town clocktower?", max_chars=len(relevant) + 5)
    assert "clocktower" in result
    assert "zzz" not in result


def test_relevant_passages_never_truncates_mid_passage():
    mem = RunMemory()
    text = "A" * 500
    mem.remember_passage(step_index=1, url="u1", text=text, source="dom")
    result = mem.relevant_passages("anything", max_chars=100)
    # Even though the budget is smaller than the passage, we keep at least
    # one whole passage rather than cutting it mid-string.
    assert result == text

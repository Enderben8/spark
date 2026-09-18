from spark.perception.ocr.base import OcrBlock
from spark.perception.page_view import (
    _has_sentence_punctuation,
    _looks_garbled,
    _looks_multi_column,
)


def test_looks_garbled_flags_symbol_soup():
    garbled = " ".join(["xqzv", "wkrpj", "zzxq", "vbnm", "qwrt", "zxcvb"])
    assert _looks_garbled(garbled) is True


def test_looks_garbled_accepts_real_prose():
    prose = (
        "The quick brown fox jumps over the lazy dog while ferns quietly "
        "reproduce by spores rather than seeds in the damp forest undergrowth."
    )
    assert _looks_garbled(prose) is False


def test_looks_garbled_needs_enough_signal():
    assert _looks_garbled("abc") is False  # too short to judge either way


def test_sentence_punctuation_detection():
    assert _has_sentence_punctuation("This is a sentence.") is True
    assert _has_sentence_punctuation("no terminal punctuation here") is False


def _block(x: float, y: float, w: float = 80, h: float = 18) -> OcrBlock:
    return OcrBlock(text="word", bbox={"x": x, "y": y, "w": w, "h": h})


def test_multi_column_detection_flags_two_clear_columns():
    blocks = []
    image_width = 800.0
    for row in range(10):
        y = row * 20
        blocks.append(_block(x=20, y=y))       # left column
        blocks.append(_block(x=420, y=y))      # right column, clear gap
    assert _looks_multi_column(blocks, image_width) is True


def test_multi_column_detection_does_not_flag_single_column():
    blocks = []
    image_width = 800.0
    for row in range(10):
        y = row * 20
        blocks.append(_block(x=20 + (row % 3) * 5, y=y))  # single wandering column
    assert _looks_multi_column(blocks, image_width) is False


def test_multi_column_detection_handles_empty_input():
    assert _looks_multi_column([], 800.0) is False

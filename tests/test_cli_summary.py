from types import SimpleNamespace

from qbo_ar.cli import _apply_summary


def line(status):
    return SimpleNamespace(status=status)


def test_summary_separates_duplicates_from_review():
    summary = SimpleNamespace(
        posted=[1, 2],
        skipped=[line("duplicate")] * 5 + [line("unmatched")],
    )
    assert _apply_summary(summary) == "Posted 2 payment(s); 5 duplicate(s) skipped; 1 need review."


def test_summary_with_nothing_skipped():
    summary = SimpleNamespace(posted=[1], skipped=[])
    assert _apply_summary(summary) == "Posted 1 payment(s); 0 duplicate(s) skipped; 0 need review."

# tests/test_undo_journal.py
from financial_mcp.cache.undo_journal import UndoJournal


def _journal(tmp_path):
    return UndoJournal(str(tmp_path / "cache.db"), clock=lambda: 1000.0)


def test_record_then_mark_succeeded_makes_row_undoable(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "tx1", "old1", "Uncategorized", "new1", "Shopping")
    assert j.last_undoable_batch() is None  # not yet succeeded
    j.mark_succeeded("b1", "tx1")
    assert j.last_undoable_batch() == "b1"
    rows = j.undoable_rows("b1")
    assert rows == [{
        "transaction_id": "tx1", "old_category_id": "old1",
        "old_category_name": "Uncategorized", "new_category_name": "Shopping",
    }]


def test_row_without_old_id_is_not_undoable(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "tx1", None, "Ambiguous", "new1", "Shopping")
    j.mark_succeeded("b1", "tx1")
    assert j.last_undoable_batch() is None


def test_multi_level_undo_steps_back_through_batches(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "tx1", "old1", "A", "new1", "Shopping"); j.mark_succeeded("b1", "tx1")
    j.record("b2", "tx2", "old2", "B", "new2", "Groceries"); j.mark_succeeded("b2", "tx2")
    assert j.last_undoable_batch() == "b2"
    j.mark_undone("b2")
    assert j.last_undoable_batch() == "b1"
    j.mark_undone("b1")
    assert j.last_undoable_batch() is None


def test_history_is_newest_first_with_summary_and_undone_flag(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "tx1", "o", "Uncategorized", "n", "Shopping"); j.mark_succeeded("b1", "tx1")
    j.record("b1", "tx2", "o", "Uncategorized", "n", "Shopping"); j.mark_succeeded("b1", "tx2")
    j.record("b2", "tx3", "o", "Dining", "n", "Groceries"); j.mark_succeeded("b2", "tx3")
    hist = j.history()
    assert [h["batch_id"] for h in hist] == ["b2", "b1"]
    assert hist[1]["count"] == 2
    assert hist[1]["summary"] == "Uncategorized→Shopping ×2"
    assert hist[0]["undone"] is False

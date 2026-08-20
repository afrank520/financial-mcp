# tests/test_undo_journal.py
from financial_mcp.cache.undo_journal import UndoJournal


def _journal(tmp_path):
    return UndoJournal(str(tmp_path / "cache.db"), clock=lambda: 1000.0)


def test_record_then_mark_succeeded_makes_row_undoable(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "recategorize", "tx1", "old1", "new1", "Uncategorized", "Shopping")
    assert j.last_undoable_batch() is None  # not yet succeeded
    j.mark_succeeded("b1", "tx1")
    assert j.last_undoable_batch() == "b1"
    rows = j.undoable_rows("b1")
    assert rows == [{
        "target_id": "tx1", "op_type": "recategorize", "old_value": "old1",
        "label_old": "Uncategorized", "label_new": "Shopping",
    }]


def test_row_without_old_id_is_not_undoable(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "recategorize", "tx1", None, "new1", "Ambiguous", "Shopping")
    j.mark_succeeded("b1", "tx1")
    assert j.last_undoable_batch() is None


def test_multi_level_undo_steps_back_through_batches(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "recategorize", "tx1", "old1", "new1", "A", "Shopping"); j.mark_succeeded("b1", "tx1")
    j.record("b2", "recategorize", "tx2", "old2", "new2", "B", "Groceries"); j.mark_succeeded("b2", "tx2")
    assert j.last_undoable_batch() == "b2"
    j.mark_undone("b2")
    assert j.last_undoable_batch() == "b1"
    j.mark_undone("b1")
    assert j.last_undoable_batch() is None


def test_history_is_newest_first_with_summary_and_undone_flag(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "recategorize", "tx1", "o", "n", "Uncategorized", "Shopping"); j.mark_succeeded("b1", "tx1")
    j.record("b1", "recategorize", "tx2", "o", "n", "Uncategorized", "Shopping"); j.mark_succeeded("b1", "tx2")
    j.record("b2", "recategorize", "tx3", "o", "n", "Dining", "Groceries"); j.mark_succeeded("b2", "tx3")
    hist = j.history()
    assert [h["batch_id"] for h in hist] == ["b2", "b1"]
    assert hist[1]["count"] == 2
    assert hist[1]["summary"] == "Uncategorized→Shopping ×2"
    assert hist[0]["undone"] is False


def test_migration_preserves_old_recat_rows(tmp_path):
    import sqlite3
    from financial_mcp.cache.undo_journal import UndoJournal
    db = str(tmp_path / "old.db")
    # Simulate a Slice-1 journal DB with the OLD schema + one succeeded row.
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE write_journal (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
        transaction_id TEXT NOT NULL, old_category_id TEXT,
        old_category_name TEXT NOT NULL, new_category_id TEXT NOT NULL,
        new_category_name TEXT NOT NULL, timestamp TEXT NOT NULL,
        undone INTEGER NOT NULL DEFAULT 0, succeeded INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""INSERT INTO write_journal
        (batch_id, transaction_id, old_category_id, old_category_name,
         new_category_id, new_category_name, timestamp, undone, succeeded)
        VALUES ('b1','tx1','oid','Dining','nid','Groceries','2026-08-01T00:00:00+00:00',0,1)""")
    conn.commit(); conn.close()

    j = UndoJournal(db)  # opening it runs the migration
    assert j.last_undoable_batch() == "b1"
    rows = j.undoable_rows("b1")
    assert rows == [{"target_id": "tx1", "op_type": "recategorize",
                     "old_value": "oid", "label_old": "Dining", "label_new": "Groceries"}]


def test_rename_row_is_undoable(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "rename_category", "cat1", "Old Name", "New Name", "Old Name", "New Name")
    j.mark_succeeded("b1", "cat1")
    assert j.last_undoable_batch() == "b1"
    assert j.undoable_rows("b1")[0]["op_type"] == "rename_category"


def test_create_row_is_recorded_but_not_undoable(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "create_category", "newcat", None, "Vacations", None, "Vacations")
    j.mark_succeeded("b1", "newcat")
    assert j.last_undoable_batch() is None          # create is never selected
    assert j.undoable_rows("b1") == []
    hist = j.history()
    assert hist[0]["summary"] == "＋Vacations (created) ×1"

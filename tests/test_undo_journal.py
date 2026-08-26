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
        "new_value": "new1", "label_old": "Uncategorized", "label_new": "Shopping",
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
                     "old_value": "oid", "new_value": "nid",
                     "label_old": "Dining", "label_new": "Groceries"}]


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


def test_delete_category_is_reversible_and_carries_new_value(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal, REVERSIBLE_OPS
    assert "delete_category" in REVERSIBLE_OPS
    j = UndoJournal(str(tmp_path / "j.db"))
    # delete op: target_id=deleted id, old_value=group_id, new_value=icon, label_old=name
    j.record("b1", "delete_category", "c1", "g1", "🛒", "Groceries", "Groceries")
    j.mark_succeeded("b1", "c1")
    assert j.last_undoable_batch() == "b1"
    rows = j.undoable_rows("b1")
    assert rows[0]["op_type"] == "delete_category"
    assert rows[0]["old_value"] == "g1"
    assert rows[0]["new_value"] == "🛒"       # additive key, needed for recreate
    assert rows[0]["label_old"] == "Groceries"


def test_pending_dependents_counts_target_and_old_value_refs(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal
    j = UndoJournal(str(tmp_path / "j.db"))
    # recat that would restore INTO c1 (old_value=c1)
    j.record("b1", "recategorize", "tx1", "c1", "c2", "Groceries", "Restaurants")
    j.mark_succeeded("b1", "tx1")
    # rename whose target IS c1 (target_id=c1)
    j.record("b2", "rename_category", "c1", "Groceries", "Food", "Groceries", "Food")
    j.mark_succeeded("b2", "c1")
    # unrelated recat
    j.record("b3", "recategorize", "tx2", "c9", "c8", "X", "Y")
    j.mark_succeeded("b3", "tx2")
    assert j.pending_dependents("c1") == 2
    assert j.pending_dependents("c9") == 1
    assert j.pending_dependents("c7") == 0
    # once undone, no longer a dependent
    j.mark_undone("b1")
    assert j.pending_dependents("c1") == 1


def test_history_summarizes_delete(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal
    j = UndoJournal(str(tmp_path / "j.db"))
    j.record("b1", "delete_category", "c1", "g1", "🛒", "Groceries", "Groceries")
    j.mark_succeeded("b1", "c1")
    assert j.history()[0]["summary"] == "✕ Groceries (deleted) ×1"


def test_rename_merchant_is_reversible(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal
    j = UndoJournal(str(tmp_path / "c.db"))
    j.record("b1", "rename_merchant", "t1", "AMZN Mktp US*2X4", "Amazon",
             "AMZN Mktp US*2X4", "Amazon")
    j.mark_succeeded("b1", "t1")
    assert j.last_undoable_batch() == "b1"
    rows = j.undoable_rows("b1")
    assert rows[0]["op_type"] == "rename_merchant"
    assert rows[0]["old_value"] == "AMZN Mktp US*2X4"


def test_history_renders_merchant_summary(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal
    j = UndoJournal(str(tmp_path / "c.db"))
    j.record("b1", "rename_merchant", "t1", "AMZN*1", "Amazon", "AMZN*1", "Amazon")
    j.mark_succeeded("b1", "t1")
    h = j.history()
    assert "(merchant)" in h[0]["summary"]
    assert "Amazon" in h[0]["summary"]


def test_create_rule_is_reversible(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal
    j = UndoJournal(str(tmp_path / "c.db"))
    j.record("b1", "create_rule", "r2", "AMZN", "Amazon", "AMZN", "Amazon")
    j.mark_succeeded("b1", "r2")
    assert j.last_undoable_batch() == "b1"
    rows = j.undoable_rows("b1")
    assert rows[0]["op_type"] == "create_rule"
    assert rows[0]["target_id"] == "r2"
    assert rows[0]["old_value"] == "AMZN"


def test_history_renders_rule_summary(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal
    j = UndoJournal(str(tmp_path / "c.db"))
    j.record("b1", "create_rule", "r2", "AMZN", "Amazon", "AMZN", "Amazon")
    j.mark_succeeded("b1", "r2")
    h = j.history()
    assert "AMZN" in h[0]["summary"] and "Amazon" in h[0]["summary"]


def test_create_category_rule_is_reversible_and_summarized(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal, REVERSIBLE_OPS

    assert "create_category_rule" in REVERSIBLE_OPS

    j = UndoJournal(str(tmp_path / "j.db"))
    # Mirror the tool's record call: target=rule_id, old/label_old=contains,
    # new/label_new=category name.
    j.record("b1", "create_category_rule", "rule9", "SHELL", "Gas", "SHELL", "Gas")
    j.mark_succeeded("b1", "rule9")

    assert j.last_undoable_batch() == "b1"
    hist = j.history()
    assert hist[0]["summary"] == "Rule: contains 'SHELL' → category 'Gas' ×1"
    assert hist[0]["undone"] is False

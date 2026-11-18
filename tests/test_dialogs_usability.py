"""UI usability regression tests for history and profile dialogs."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from chest_scanner.gui import dialogs


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_history_filters_are_labeled_and_comparison_needs_two_scans(
        qapp, monkeypatch):
    scans = [
        {"timestamp": "01.11.2026 10:00:00", "occupied_slots": 3,
         "unique_items": 2, "totals": {"Diamond": 10}},
        {"timestamp": "02.11.2026 10:00:00", "occupied_slots": 4,
         "unique_items": 3, "totals": {"Diamond": 20}},
        {"timestamp": "03.11.2026 10:00:00", "occupied_slots": 5,
         "unique_items": 4, "totals": {"Stone": 30}},
    ]
    monkeypatch.setattr(dialogs, "load_all_chests", lambda: scans)
    window = dialogs.HistoryDialog()

    labels = [label.text() for label in window.findChildren(QLabel)]
    assert "\u041c\u0438\u043d. \u043a\u043e\u043b-\u0432\u043e:" in labels
    assert "\u0414\u0430\u0442\u0430 \u0434\u043e:" in labels
    assert window.qty_edit.placeholderText() == "\u041c\u0438\u043d.\u043a\u043e\u043b-\u0432\u043e"
    assert not window.compare_btn.isEnabled()
    assert window.total_lbl.text() == "\u041f\u043e\u043a\u0430\u0437\u0430\u043d\u043e: 3 \u0438\u0437 3"

    window.listbox.item(0).setSelected(True)
    assert not window.compare_btn.isEnabled()
    window.listbox.item(1).setSelected(True)
    assert window.compare_btn.isEnabled()

    window.qty_edit.setText("15")
    window._refresh_list()
    assert len(window._filtered) == 2
    assert window.total_lbl.text() == "\u041f\u043e\u043a\u0430\u0437\u0430\u043d\u043e: 2 \u0438\u0437 3"
    assert not window.compare_btn.isEnabled()
    window.close()


def test_profile_actions_follow_selection_and_active_state(qapp, monkeypatch):
    profile_data = {
        "active": "Default",
        "profiles": [
            {"name": "Default"},
            {"name": "Survival"},
            {"name": "Creative"},
        ],
    }
    monkeypatch.setattr(dialogs, "load_profiles", lambda: profile_data)
    window = dialogs.ProfileManagerDialog(SimpleNamespace())

    assert window.active_label.text() == "\u0422\u0435\u043a\u0443\u0449\u0438\u0439 \u043f\u0440\u043e\u0444\u0438\u043b\u044c: Default"
    assert window._chosen() == "Default"
    assert not window.switch_btn.isEnabled()
    assert not window.rename_btn.isEnabled()
    assert not window.delete_btn.isEnabled()
    assert window.create_btn.isEnabled()

    window.listbox.setCurrentRow(1)
    assert window._chosen() == "Survival"
    assert window.switch_btn.isEnabled()
    assert window.rename_btn.isEnabled()
    assert window.delete_btn.isEnabled()

    window.listbox.setCurrentRow(2)
    assert window._chosen() == "Creative"
    assert window.delete_btn.isEnabled()
    window.close()



def test_export_actions_are_disabled_when_history_is_empty(qapp, monkeypatch):
    from PySide6.QtWidgets import QPushButton

    monkeypatch.setattr(dialogs, "load_all_chests", lambda **_kwargs: [])
    window = dialogs.ExportDialog()
    buttons = [button for button in window.findChildren(QPushButton)
               if button.text() in {"CSV", "JSON"}]
    assert len(buttons) == 3
    assert all(not button.isEnabled() for button in buttons)
    assert all(button.toolTip() == "\u041d\u0435\u0442 \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0439 \u0434\u043b\u044f \u044d\u043a\u0441\u043f\u043e\u0440\u0442\u0430" for button in buttons)
    labels = [label.text() for label in window.findChildren(QLabel)]
    assert "\u041f\u043e\u043a\u0430 \u043d\u0435\u0442 \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0439 \u0434\u043b\u044f \u044d\u043a\u0441\u043f\u043e\u0440\u0442\u0430." in labels
    window.close()


def test_statistics_panels_show_empty_state(qapp, monkeypatch):
    from PySide6.QtWidgets import QTableWidget

    from chest_scanner import storage

    monkeypatch.setattr(storage, "sync_from_json", lambda *_args: None)
    monkeypatch.setattr(storage, "stats", lambda *_args: {
        "chests": 0, "items_total": 0, "unique_items": 0,
        "top_qty": [], "top_freq": [],
    })
    window = dialogs.StatsDialog()
    tables = window.findChildren(QTableWidget)
    assert len(tables) == 2
    assert all(table.isHidden() for table in tables)
    labels = [label.text() for label in window.findChildren(QLabel)]
    assert labels.count(
        "\u041f\u043e\u043a\u0430 \u043d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445. \u0412\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u0435 \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0439.") == 2
    hints = [label for label in window.findChildren(QLabel)
             if label.text().startswith("\u041f\u043e\u043a\u0430 \u043d\u0435\u0442")]
    assert len(hints) == 2 and all(not hint.isHidden() for hint in hints)
    window.close()



def test_statistics_tables_are_shown_when_data_exists(qapp, monkeypatch):
    from PySide6.QtWidgets import QTableWidget

    from chest_scanner import storage

    monkeypatch.setattr(storage, "sync_from_json", lambda *_args: None)
    monkeypatch.setattr(storage, "stats", lambda *_args: {
        "chests": 4, "items_total": 128, "unique_items": 12,
        "top_qty": [("Diamond", 64)],
        "top_freq": [("Stone", 3)],
    })
    window = dialogs.StatsDialog()
    tables = window.findChildren(QTableWidget)
    assert len(tables) == 2
    assert all(not table.isHidden() for table in tables)
    assert [tables[0].item(0, col).text() for col in range(2)] == ["Diamond", "64"]
    assert [tables[1].item(0, col).text() for col in range(2)] == ["Stone", "3"]
    assert tables[0].columnWidth(0) == 150
    assert tables[0].columnWidth(1) == 80
    window.close()


def test_history_details_use_each_items_configured_stack_size(qapp, monkeypatch):
    scans = [{
        "timestamp": "01.11.2026 10:00:00",
        "occupied_slots": 2,
        "unique_items": 2,
        "totals": {"CustomStack": 32, "DefaultStack": 128, "UnknownCount": None},
        "categories": {},
        "slots": [{"item": "UnknownCount", "quantity": None}],
    }]
    monkeypatch.setattr(dialogs, "load_all_chests", lambda: scans)
    monkeypatch.setattr(
        dialogs, "load_json",
        lambda path, default=None: {
            "items": [
                {"name": "CustomStack", "stack_size": 16},
                {"name": "DefaultStack", "stack_size": 64},
            ]
        } if path == dialogs.paths.ITEMS_FILE else default,
    )
    window = dialogs.HistoryDialog()
    window.listbox.setCurrentRow(0)

    rows = {}
    for row in range(window.table.rowCount()):
        name = window.table.item(row, 0).text()
        if name != "UnknownCount":
            rows[name] = (
                window.table.item(row, 1).text(),
                window.table.item(row, 2).text(),
            )

    assert rows == {"CustomStack": ("32", "2"), "DefaultStack": ("128", "2")}
    assert window.table.rowCount() == 3  # the unknown quantity remains visible
    unknown_row = next(
        row for row in range(window.table.rowCount())
        if window.table.item(row, 0).text() == "UnknownCount"
    )
    assert window.table.item(unknown_row, 1).text() == "\u041d\u0435 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d\u043e"
    assert window.table.item(unknown_row, 2).text() == "\u2014"
    window.close()


def test_comparison_dialog_shows_stack_counts_and_unknown_quantities(qapp, monkeypatch):
    monkeypatch.setattr(
        dialogs, "load_json",
        lambda path, default=None: {
            "items": [{"name": "CustomStack", "stack_size": 16}]
        } if path == dialogs.paths.ITEMS_FILE else default,
    )
    old = {
        "timestamp": "01.11.2026 10:00:00",
        "totals": {"CustomStack": 32, "Unreadable": None},
        "slots": [{"item": "Unreadable", "quantity": None}],
    }
    new = {
        "timestamp": "02.11.2026 10:00:00",
        "totals": {"CustomStack": 48},
        "slots": [],
    }
    window = dialogs.ComparisonDialog(old, new)
    assert window.table.columnCount() == 7
    rows = {
        window.table.item(row, 0).text(): row
        for row in range(window.table.rowCount())
    }
    custom_row = rows["CustomStack"]
    assert window.table.item(custom_row, 2).text() == "2"
    assert window.table.item(custom_row, 4).text() == "3"
    unknown_row = rows["Unreadable"]
    assert window.table.item(unknown_row, 1).text() == "\u041d\u0435 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d\u043e"
    assert window.table.item(unknown_row, 5).text() == "\u2014"
    window.close()



def test_export_dialog_disables_export_when_history_is_incomplete(qapp, monkeypatch):
    from PySide6.QtWidgets import QPushButton

    def fail_load(**_kwargs):
        raise ValueError("broken history archive")

    monkeypatch.setattr(dialogs, "load_all_chests", fail_load)
    window = dialogs.ExportDialog()
    buttons = [button for button in window.findChildren(QPushButton)
               if button.text() in {"CSV", "JSON"}]

    assert window.load_error == "broken history archive"
    assert len(buttons) == 3
    assert all(not button.isEnabled() for button in buttons)
    restore_buttons = [button for button in window.findChildren(QPushButton)
                       if button.text() == "Восстановить историю из JSON"]
    assert len(restore_buttons) == 1 and not restore_buttons[0].isEnabled()
    labels = [label.text() for label in window.findChildren(QLabel)]
    assert any("broken history archive" in label for label in labels)
    window.close()



def test_export_dialog_restores_json_after_confirmation_and_syncs_cache(
        qapp, monkeypatch, tmp_path):
    import json

    from PySide6.QtWidgets import QPushButton

    results_file = tmp_path / "results" / "chests.json"
    history_dir = tmp_path / "results" / "history"
    history_dir.mkdir(parents=True)
    db_file = tmp_path / "results" / "chests.db"
    backup_file = tmp_path / "export.json"
    backup_file.write_text(json.dumps([{
        "id": "restored-1", "timestamp": "10.11.2026 10:00:00",
        "slots": [{"item": "Mystery", "quantity": None}],
        "totals": {"Mystery": None}, "unknown_items": ["Mystery"],
    }]), encoding="utf-8")

    monkeypatch.setattr(dialogs.paths, "RESULTS_FILE", str(results_file))
    monkeypatch.setattr(dialogs.paths, "HISTORY_DIR", str(history_dir))
    monkeypatch.setattr(dialogs.paths, "RESULTS_DB", str(db_file))
    monkeypatch.setattr(dialogs.QFileDialog, "getOpenFileName",
                        lambda *_args, **_kwargs: (str(backup_file), "JSON (*.json)"))
    monkeypatch.setattr(dialogs, "_question", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(dialogs.QMessageBox, "information", lambda *_args, **_kwargs: None)

    window = dialogs.ExportDialog()
    restore_button = next(button for button in window.findChildren(QPushButton)
                          if button.text() == "Восстановить историю из JSON")
    assert restore_button.isEnabled()
    window._restore_json()

    restored = json.loads(results_file.read_text(encoding="utf-8"))["chests"]
    assert len(restored) == 1 and restored[0]["id"] == "restored-1"
    assert restored[0]["totals"]["Mystery"] is None
    assert db_file.exists()
    assert window.chests[0]["id"] == "restored-1"
    window.close()


def test_export_dialog_recovers_from_unexpected_history_loader_error(
        qapp, monkeypatch):
    from PySide6.QtWidgets import QPushButton

    def fail_load(**_kwargs):
        raise RuntimeError("unexpected history failure")

    monkeypatch.setattr(dialogs, "load_all_chests", fail_load)
    window = dialogs.ExportDialog()

    buttons = [button for button in window.findChildren(QPushButton)
               if button.text() in {"CSV", "JSON"}]
    assert window.chests == []
    assert window.load_error == "unexpected history failure"
    assert len(buttons) == 3
    assert all(not button.isEnabled() for button in buttons)
    window.close()


def test_history_restore_reports_unexpected_prepare_error(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(dialogs, "load_all_chests", lambda **_kwargs: [])
    window = dialogs.ExportDialog()
    monkeypatch.setattr(
        dialogs.QFileDialog, "getOpenFileName",
        lambda *_args, **_kwargs: ("backup.json", "JSON (*.json)"))
    monkeypatch.setattr(
        dialogs, "results_history_guard",
        lambda *_args, **_kwargs: __import__("contextlib").nullcontext())
    monkeypatch.setattr(
        dialogs, "load_all_chests",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("prepare failed")))
    messages = []
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda *_args: messages.append(_args[-1]))

    dialogs.ExportDialog._restore_json(window)

    assert messages == ["prepare failed"]
    window.close()


def test_history_restore_reports_unexpected_apply_error(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window = dialogs.ExportDialog()
    monkeypatch.setattr(
        dialogs.QFileDialog, "getOpenFileName",
        lambda *_args, **_kwargs: ("backup.json", "JSON (*.json)"))
    monkeypatch.setattr(
        dialogs, "results_history_guard",
        lambda *_args, **_kwargs: __import__("contextlib").nullcontext())
    monkeypatch.setattr(dialogs, "load_all_chests", lambda **_kwargs: [])
    monkeypatch.setattr(dialogs, "prepare_history_restore",
                        lambda *_args: {"added": 1, "skipped_existing": 0,
                                        "skipped_duplicates": 0})
    monkeypatch.setattr(dialogs, "_question", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        dialogs, "apply_history_restore",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("apply failed")))
    messages = []
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda *_args: messages.append(_args[-1]))

    dialogs.ExportDialog._restore_json(window)

    assert messages == ["apply failed"]
    window.close()


def test_profile_create_reports_committed_operation_when_list_refresh_fails(
        qapp, monkeypatch):
    profile_data = {
        "active": "Default",
        "profiles": [{"name": "Default"}],
    }
    calls = 0

    def load_registry():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("registry temporarily unavailable")
        return profile_data

    monkeypatch.setattr(dialogs, "load_profiles", load_registry)
    monkeypatch.setattr(dialogs.QInputDialog, "getText",
                        lambda *_args, **_kwargs: ("Survival", True))
    created = []
    monkeypatch.setattr(dialogs, "create_profile_data",
                        lambda name: created.append(name))
    warnings = []
    monkeypatch.setattr(dialogs.QMessageBox, "warning",
                        lambda *_args: warnings.append(_args[-1]))
    main = SimpleNamespace(_log=lambda *_args: None)
    window = dialogs.ProfileManagerDialog(main)

    window._create()

    assert created == ["Survival"]
    assert calls == 2
    assert len(warnings) == 1
    assert "???????? ? ???????? ?????????" in warnings[0]
    window.close()



def test_stats_dialog_refuses_partial_history_without_replacing_cache(qapp, monkeypatch):
    calls = []

    def broken_history(strict=False):
        assert strict is True
        raise ValueError("broken archive")

    monkeypatch.setattr(dialogs, "load_all_chests", broken_history)
    monkeypatch.setattr(dialogs.storage, "sync_from_json",
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    window = dialogs.StatsDialog()
    try:
        assert "broken archive" in window.load_error
        assert calls == []
        labels = [label.text() for label in window.findChildren(QLabel)]
        assert any("SQLite-??? ?? ???????????" in label for label in labels)
    finally:
        window.close()

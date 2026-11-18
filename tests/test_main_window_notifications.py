from chest_scanner.gui.main_window import _format_scan_notification


def test_scan_notification_uses_a_real_line_break_for_unknown_items():
    message = _format_scan_notification(
        {"occupied_slots": 12, "unique_items": 4}, unknown_count=2
    )

    assert message.count("\n") == 1
    assert "\\n" not in message
    assert message.endswith(": 2")


def test_scan_notification_omits_unknown_line_when_there_are_no_unknown_items():
    message = _format_scan_notification(
        {"occupied_slots": 0, "unique_items": 0}, unknown_count=0
    )

    assert "\n" not in message



def test_dashboard_stats_include_archives_and_presence_only_items(monkeypatch):
    import contextlib
    from types import SimpleNamespace

    import chest_scanner.gui.main_window as main_window

    values = {}
    window = SimpleNamespace(
        cards={key: SimpleNamespace(set_value=lambda value, key=key: values.__setitem__(key, value))
               for key in ("chests", "items", "unique", "unknown")},
        _sync_undo_scan_btn=lambda: None,
    )
    history = [
        {"slots": [
            {"item": "known", "quantity": 5},
            {"item": "unknown-count", "quantity": None, "unknown": True},
        ], "totals": {"known": 5}, "unknown_items": ["unknown-count"]},
        {"slots": [{"item": "old-item", "quantity": 2}],
         "totals": {"old-item": 2}, "unknown_items": []},
    ]
    monkeypatch.setattr(main_window, "load_all_chests", lambda strict=False: history)
    monkeypatch.setattr(main_window, "results_history_guard",
                        lambda _path: contextlib.nullcontext())
    main_window.ScannerGUI._refresh_stats(window)
    assert values == {"chests": 2, "items": "7", "unique": 3, "unknown": 1}

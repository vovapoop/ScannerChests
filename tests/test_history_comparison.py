from chest_scanner.gui.dialogs import _comparison_rows, _scan_sort_key


def test_comparison_distinguishes_added_removed_and_quantity_changes():
    rows, counts, net = _comparison_rows(
        {"old": 12, "more": 8, "less": 20, "same": 4},
        {"new": 7, "more": 15, "less": 5, "same": 4},
    )

    by_name = {row[0]: row for row in rows}
    assert by_name["new"][4] == "added"
    assert by_name["old"][4] == "removed"
    assert by_name["more"][4] == "increased"
    assert by_name["less"][4] == "decreased"
    assert by_name["same"][4] == "same"
    assert counts == {
        "added": 1, "removed": 1, "increased": 1,
        "decreased": 1, "same": 1, "unknown": 0,
    }
    assert net == -13


def test_comparison_handles_missing_or_invalid_totals():
    rows, counts, net = _comparison_rows(
        {"broken": "not-a-number", "gone": None}, None
    )
    assert rows == [
        ("broken", None, 0, None, "removed"),
        ("gone", None, 0, None, "removed"),
    ]
    assert counts["removed"] == 2
    assert net == 0


def test_scan_sort_key_orders_valid_timestamps_chronologically():
    older = {"timestamp": "01.11.2026 10:00:00"}
    newer = {"timestamp": "02.11.2026 10:00:00"}
    assert sorted([newer, older], key=_scan_sort_key) == [older, newer]




def test_history_loader_includes_current_and_archived_results(tmp_path, monkeypatch):
    import json

    from chest_scanner import paths
    from chest_scanner.gui.dialogs import load_all_chests

    history_dir = tmp_path / "history"
    history_dir.mkdir()
    results_file = tmp_path / "chests.json"
    results_file.write_text(
        json.dumps({"chests": [
            {"id": "current", "timestamp": "03.11.2026 10:00:00"}
        ]}),
        encoding="utf-8",
    )
    (history_dir / "report_old.json").write_text(
        json.dumps({"chests": [
            {"id": "archived", "timestamp": "01.11.2026 10:00:00"}
        ]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "RESULTS_FILE", str(results_file))
    monkeypatch.setattr(paths, "HISTORY_DIR", str(history_dir))

    scans = load_all_chests()

    assert [scan["id"] for scan in scans] == ["current", "archived"]
    assert scans[1]["_source"] == "report_old.json"


def test_history_loader_deduplicates_recovered_archive_and_keeps_current(tmp_path, monkeypatch):
    import json

    from chest_scanner import paths
    from chest_scanner.gui.dialogs import load_all_chests

    history_dir = tmp_path / "history"
    history_dir.mkdir()
    results_file = tmp_path / "chests.json"
    current = {"id": "same-id", "timestamp": "03.11.2026 10:00:00", "note": "current"}
    archived = {"id": "same-id", "timestamp": "03.11.2026 10:00:00", "note": "archive"}
    unique = {"id": "archived-only", "timestamp": "01.11.2026 10:00:00"}
    results_file.write_text(json.dumps({"chests": [current]}), encoding="utf-8")
    for filename in ("report_recovered.json", "report_duplicate.json"):
        (history_dir / filename).write_text(
            json.dumps({"chests": [archived, unique, None]}), encoding="utf-8"
        )
    monkeypatch.setattr(paths, "RESULTS_FILE", str(results_file))
    monkeypatch.setattr(paths, "HISTORY_DIR", str(history_dir))

    scans = load_all_chests()

    assert [scan["id"] for scan in scans] == ["same-id", "archived-only"]
    assert scans[0]["note"] == "current"
    assert scans[1]["_source"] == "report_duplicate.json"


def test_comparison_keeps_unknown_quantities_out_of_numeric_differences():
    from chest_scanner.gui.dialogs import _comparison_snapshot

    old, old_unknown = _comparison_snapshot({
        "totals": {"A": 10, "B": None, "C": 4},
        "slots": [{"item": "B", "quantity": None}],
    })
    new, new_unknown = _comparison_snapshot({
        "totals": {"A": 15, "B": 3},
        "slots": [{"item": "D", "quantity": None}],
    })
    rows, counts, net = _comparison_rows(old, new, old_unknown, new_unknown)
    by_name = {row[0]: row for row in rows}

    assert by_name["A"] == ("A", 10, 15, 5, "increased")
    assert by_name["B"] == ("B", None, 3, None, "unknown")
    assert by_name["C"] == ("C", 4, 0, -4, "removed")
    assert by_name["D"] == ("D", 0, None, None, "added")
    assert counts["unknown"] == 1
    assert counts["added"] == 1
    assert net == 1


def test_comparison_snapshot_sums_slots_when_totals_are_missing():
    from chest_scanner.gui.dialogs import _comparison_snapshot

    totals, unknown = _comparison_snapshot({
        "slots": [
            {"item": "Stacked", "quantity": 16},
            {"item": "Stacked", "quantity": 16},
            {"item": "Unreadable", "quantity": None},
        ]
    })
    assert totals["Stacked"] == 32
    assert totals["Unreadable"] is None
    assert unknown == {"Unreadable"}



def test_strict_history_export_rejects_corrupt_archive_instead_of_exporting_partial_data(
        tmp_path, monkeypatch):
    import json

    import pytest

    from chest_scanner import paths
    from chest_scanner.gui.dialogs import load_all_chests

    history_dir = tmp_path / "history"
    history_dir.mkdir()
    results_file = tmp_path / "chests.json"
    results_file.write_text(json.dumps({"chests": [{"id": "current"}]}),
                            encoding="utf-8")
    (history_dir / "report_broken.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(paths, "RESULTS_FILE", str(results_file))
    monkeypatch.setattr(paths, "HISTORY_DIR", str(history_dir))

    with pytest.raises(ValueError, match="report_broken.json"):
        load_all_chests(strict=True)

    # The regular history view remains available and keeps its legacy tolerant behavior.
    assert [scan["id"] for scan in load_all_chests()] == ["current"]


def test_strict_history_export_rejects_invalid_record_shape(tmp_path, monkeypatch):
    import json

    import pytest

    from chest_scanner import paths
    from chest_scanner.gui.dialogs import load_all_chests

    history_dir = tmp_path / "history"
    history_dir.mkdir()
    results_file = tmp_path / "chests.json"
    results_file.write_text(json.dumps({"chests": [{"id": "ok"}, None]}),
                            encoding="utf-8")
    monkeypatch.setattr(paths, "RESULTS_FILE", str(results_file))
    monkeypatch.setattr(paths, "HISTORY_DIR", str(history_dir))

    with pytest.raises(ValueError, match="chests.json"):
        load_all_chests(strict=True)


def test_strict_history_export_rejects_invalid_nested_collections(tmp_path, monkeypatch):
    import json

    import pytest

    from chest_scanner import paths
    from chest_scanner.gui.dialogs import load_all_chests

    history_dir = tmp_path / "history"
    history_dir.mkdir()
    results_file = tmp_path / "chests.json"
    results_file.write_text(json.dumps({"chests": [{"id": "bad", "totals": []}]}),
                            encoding="utf-8")
    monkeypatch.setattr(paths, "RESULTS_FILE", str(results_file))
    monkeypatch.setattr(paths, "HISTORY_DIR", str(history_dir))

    with pytest.raises(ValueError, match="totals"):
        load_all_chests(strict=True)


def test_scan_sort_key_places_invalid_timestamps_after_valid_dates():
    valid = {"timestamp": "01.01.2026 00:00:00"}
    invalid = {"timestamp": "not-a-date"}
    assert sorted([invalid, valid], key=_scan_sort_key) == [valid, invalid]

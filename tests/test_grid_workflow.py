"""Тесты сетки и сохранения результатов (без реального экрана)."""
import json
import os

import numpy as np
import pytest

from chest_scanner.scanner.grid import GridMixin


class GridStub(GridMixin):
    def __init__(self, ref_w, ref_h, grid):
        self.ref_w, self.ref_h = ref_w, ref_h
        self.grid = grid


def _screen_1080p():
    return np.zeros((1080, 1920, 3), dtype=np.uint8)


def test_get_grid_rect_scaling():
    g = GridStub(1920, 1080, {"x1": 100, "y1": 100, "x2": 500, "y2": 400,
                              "columns": 9, "rows": 6})
    rect = g.get_grid_rect(np.zeros((2160, 3840, 3), dtype=np.uint8))
    assert rect == {"x": 200, "y": 200, "width": 800, "height": 600,
                    "columns": 9, "rows": 6}


def test_get_grid_rect_swaps_inverted_corners():
    g = GridStub(1920, 1080, {"x1": 500, "y1": 400, "x2": 100, "y2": 100,
                              "columns": 9, "rows": 6})
    rect = g.get_grid_rect(_screen_1080p())
    assert rect["x"] == 100 and rect["y"] == 100
    assert rect["width"] == 400 and rect["height"] == 300


def test_get_slots_count_and_order():
    g = GridStub(1920, 1080, {"x1": 0, "y1": 0, "x2": 1920, "y2": 1080,
                              "columns": 9, "rows": 6})
    slots = g.get_slots(_screen_1080p())
    assert len(slots) == 54
    assert slots[0]["row"] == 0 and slots[0]["column"] == 0
    assert slots[-1]["row"] == 5 and slots[-1]["column"] == 8


def test_save_and_archive_results(tmp_path, monkeypatch):
    from chest_scanner import paths as paths_mod
    from chest_scanner.scanner.workflow import WorkflowMixin

    results_file = os.path.join(str(tmp_path), "chests.json")
    results_db = os.path.join(str(tmp_path), "chests.db")
    history_dir = os.path.join(str(tmp_path), "history")
    os.makedirs(history_dir, exist_ok=True)

    monkeypatch.setattr(paths_mod, "RESULTS_FILE", results_file)
    monkeypatch.setattr(paths_mod, "RESULTS_DB", results_db)
    monkeypatch.setattr(paths_mod, "HISTORY_DIR", history_dir)

    wf = WorkflowMixin()
    wf.MAX_RESULTS_FILE_BYTES = 1  # архивируем при следующей записи

    cd = {"id": "1", "timestamp": "01.01.2026 12:00:00",
          "occupied_slots": 3, "unique_items": 2, "unknown_items": [],
          "screen_resolution": [1920, 1080],
          "slots": [], "totals": {"Алмаз": 2}, "categories": {}}
    wf.save_chest_result(cd)
    assert os.path.exists(results_file)
    wf.save_chest_result({**cd, "id": "2"})

    archs = [f for f in os.listdir(history_dir) if f.endswith(".json")]
    assert len(archs) == 1
    # После архивации в основном файле остался только последний скан.
    data = __import__("json").load(open(results_file, encoding="utf-8"))
    assert [c["id"] for c in data["chests"]] == ["2"]

def test_get_grid_rect_rejects_grid_outside_capture_bounds():
    screen = _screen_1080p()
    for grid in (
        {"x1": -10, "y1": 10, "x2": 100, "y2": 100,
         "columns": 9, "rows": 6},
        {"x1": 1800, "y1": 10, "x2": 2000, "y2": 100,
         "columns": 9, "rows": 6},
        {"x1": 10, "y1": 1000, "x2": 100, "y2": 1100,
         "columns": 9, "rows": 6},
    ):
        reader = GridStub(1920, 1080, grid)
        rect = reader.get_grid_rect(screen)
        assert rect["width"] == rect["height"] == 0
        assert reader.get_slots(screen) == []


def test_get_grid_rect_rejects_non_finite_and_invalid_geometry():
    screen = _screen_1080p()
    invalid_grids = (
        {"x1": float("nan"), "y1": 10, "x2": 100, "y2": 100,
         "columns": 9, "rows": 6},
        {"x1": 10, "y1": 10, "x2": float("inf"), "y2": 100,
         "columns": 9, "rows": 6},
        {"x1": 10, "y1": 10, "x2": 100, "y2": 100,
         "columns": 9.5, "rows": 6},
        {"x1": 10, "y1": 10, "x2": 100, "y2": 100,
         "columns": 9, "rows": 0},
    )
    for grid in invalid_grids:
        reader = GridStub(1920, 1080, grid)
        assert reader.get_grid_rect(screen)["width"] == 0
        assert reader.get_slots(screen) == []


def test_get_grid_rect_keeps_fractional_resolution_scaling_inside_bounds():
    reader = GridStub(1920, 1080, {
        "x1": 100, "y1": 100, "x2": 500, "y2": 400,
        "columns": 9, "rows": 6,
    })
    screen = np.zeros((1350, 2400, 3), dtype=np.uint8)
    rect = reader.get_grid_rect(screen)
    assert rect == {"x": 125, "y": 125, "width": 500, "height": 375,
                    "columns": 9, "rows": 6}
    assert len(reader.get_slots(screen)) == 54


def test_save_chest_result_preserves_corrupt_json(tmp_path, monkeypatch):
    from chest_scanner import paths as paths_mod
    from chest_scanner.scanner.workflow import WorkflowMixin

    results_file = tmp_path / "chests.json"
    original = b'{"chests": [broken json'
    results_file.write_bytes(original)
    monkeypatch.setattr(paths_mod, "RESULTS_FILE", str(results_file))
    monkeypatch.setattr(paths_mod, "RESULTS_DB", str(tmp_path / "chests.db"))

    assert WorkflowMixin().save_chest_result({"id": "new"}) is False
    assert results_file.read_bytes() == original
    assert not (tmp_path / "chests.db").exists()


def test_save_chest_result_preserves_invalid_json_structure(tmp_path, monkeypatch):
    from chest_scanner import paths as paths_mod
    from chest_scanner.scanner.workflow import WorkflowMixin

    results_file = tmp_path / "chests.json"
    original = json.dumps({"chests": {"unexpected": "object"}}, ensure_ascii=False)
    results_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(paths_mod, "RESULTS_FILE", str(results_file))
    monkeypatch.setattr(paths_mod, "RESULTS_DB", str(tmp_path / "chests.db"))

    assert WorkflowMixin().save_chest_result({"id": "new"}) is False
    assert results_file.read_text(encoding="utf-8") == original
    assert not (tmp_path / "chests.db").exists()


def test_archive_retry_reuses_archive_after_interrupted_clear(tmp_path, monkeypatch):
    import json

    from chest_scanner import paths as paths_mod
    from chest_scanner.scanner import workflow as workflow_mod
    from chest_scanner.scanner.workflow import WorkflowMixin

    results_file = tmp_path / "chests.json"
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    original_scan = {"id": "before-crash", "timestamp": "01.01.2026 12:00:00"}
    results_file.write_text(json.dumps({"chests": [original_scan]}), encoding="utf-8")
    monkeypatch.setattr(paths_mod, "RESULTS_FILE", str(results_file))
    monkeypatch.setattr(paths_mod, "RESULTS_DB", str(tmp_path / "chests.db"))
    monkeypatch.setattr(paths_mod, "HISTORY_DIR", str(history_dir))

    wf = WorkflowMixin()
    wf.MAX_RESULTS_FILE_BYTES = 1
    real_save_json = workflow_mod.save_json

    def fail_source_clear(path, data, *args, **kwargs):
        if path == str(results_file) and data == {"chests": []}:
            raise OSError("simulated interruption")
        return real_save_json(path, data, *args, **kwargs)

    monkeypatch.setattr(workflow_mod, "save_json", fail_source_clear)
    assert wf.save_chest_result({"id": "new"}) is False
    archives = list(history_dir.glob("report_*.json"))
    assert len(archives) == 1
    assert json.loads(results_file.read_text(encoding="utf-8"))["chests"] == [original_scan]

    monkeypatch.setattr(workflow_mod, "save_json", real_save_json)
    assert wf.save_chest_result({"id": "new"}) is True
    archives = list(history_dir.glob("report_*.json"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text(encoding="utf-8"))["chests"] == [original_scan]
    assert [c["id"] for c in json.loads(results_file.read_text(encoding="utf-8"))["chests"]] == ["new"]



def test_concurrent_scan_saves_keep_every_result(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from chest_scanner import paths as paths_mod
    from chest_scanner.scanner.workflow import WorkflowMixin

    results_file = tmp_path / "chests.json"
    db_file = tmp_path / "chests.db"
    monkeypatch.setattr(paths_mod, "RESULTS_FILE", str(results_file))
    monkeypatch.setattr(paths_mod, "RESULTS_DB", str(db_file))
    monkeypatch.setattr(paths_mod, "HISTORY_DIR", str(tmp_path / "history"))
    workflow_a = WorkflowMixin()
    workflow_b = WorkflowMixin()
    workflows = [workflow_a, workflow_b]

    def save(index):
        scan = {
            "id": f"parallel-{index}",
            "timestamp": f"09.11.2026 23:{index % 60:02d}:00",
            "occupied_slots": 1,
            "unique_items": 1,
            "unknown_items": [],
            "screen_resolution": [1920, 1080],
            "slots": [],
            "totals": {"Diamond": 1},
            "categories": {},
        }
        return workflows[index % len(workflows)].save_chest_result(scan)

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(save, range(36)))

    assert all(outcomes)
    saved = json.loads(results_file.read_text(encoding="utf-8"))["chests"]
    assert len(saved) == 36
    assert {entry["id"] for entry in saved} == {f"parallel-{i}" for i in range(36)}


def test_save_chest_result_is_idempotent_for_current_and_archived_ids(
        tmp_path, monkeypatch):
    from chest_scanner import paths as paths_mod
    from chest_scanner import storage
    from chest_scanner.scanner.workflow import WorkflowMixin

    results_file = tmp_path / "chests.json"
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    db_file = tmp_path / "chests.db"
    monkeypatch.setattr(paths_mod, "RESULTS_FILE", str(results_file))
    monkeypatch.setattr(paths_mod, "RESULTS_DB", str(db_file))
    monkeypatch.setattr(paths_mod, "HISTORY_DIR", str(history_dir))
    storage.init_db(str(db_file))

    workflow = WorkflowMixin()
    original = {"id": "stable-id", "timestamp": "10.11.2026 12:00:00",
                "slots": [], "totals": {"Stone": 3}, "unknown_items": []}
    conflicting_retry = {**original, "totals": {"Diamond": 99}}

    assert workflow.save_chest_result(original) is True
    assert workflow.save_chest_result(conflicting_retry) is True
    assert json.loads(results_file.read_text(encoding="utf-8"))["chests"] == [
        original]
    assert storage.load_all(str(db_file)) == [original]

    workflow.MAX_RESULTS_FILE_BYTES = 1
    assert workflow.save_chest_result(original) is True
    archives = list(history_dir.glob("report_*.json"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text(encoding="utf-8"))["chests"] == [
        original]
    assert json.loads(results_file.read_text(encoding="utf-8"))["chests"] == []
    assert storage.load_all(str(db_file)) == [original]


def test_save_json_uses_unique_temporary_files(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from chest_scanner.commons import save_json

    target = tmp_path / "shared.json"

    def write(index):
        save_json(str(target), {"writer": index, "payload": [index] * 100})

    with ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(write, range(30)))

    result = json.loads(target.read_text(encoding="utf-8"))
    assert result["writer"] in range(30)
    assert result["payload"] == [result["writer"]] * 100
    assert list(tmp_path.glob(".shared.json.*.tmp")) == []



def test_save_json_retries_temporary_windows_file_lock(tmp_path, monkeypatch):
    from chest_scanner import commons

    target = tmp_path / "locked.json"
    target.write_text('{"old": true}', encoding="utf-8")
    real_replace = commons.os.replace
    attempts = {"count": 0}

    def transient_lock(source, destination):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise PermissionError("simulated transient Windows file lock")
        return real_replace(source, destination)

    monkeypatch.setattr(commons.os, "replace", transient_lock)
    commons.save_json(str(target), {"new": True})

    assert attempts["count"] == 2
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert list(tmp_path.glob(".locked.json.*.tmp")) == []


@pytest.mark.parametrize("archive_content", [
    "{broken-json",
    '{"chests": "not-a-list"}',
    '{"chests": [null]}',
])
def test_save_fails_closed_when_archive_cannot_be_checked_for_duplicate_ids(
        tmp_path, monkeypatch, archive_content):
    from chest_scanner import paths as paths_mod
    from chest_scanner.scanner.workflow import WorkflowMixin

    results_file = tmp_path / "chests.json"
    original = {"id": "current", "timestamp": "01.01.2026 10:00:00"}
    results_file.write_text(json.dumps({"chests": [original]}), encoding="utf-8")
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    (history_dir / "report_damaged.json").write_text(
        archive_content, encoding="utf-8")
    monkeypatch.setattr(paths_mod, "RESULTS_FILE", str(results_file))
    monkeypatch.setattr(paths_mod, "RESULTS_DB", str(tmp_path / "chests.db"))
    monkeypatch.setattr(paths_mod, "HISTORY_DIR", str(history_dir))

    assert WorkflowMixin().save_chest_result({"id": "retry-id"}) is False
    assert json.loads(results_file.read_text(encoding="utf-8"))["chests"] == [original]

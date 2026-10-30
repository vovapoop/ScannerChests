"""Тесты сетки и сохранения результатов (без реального экрана)."""
import os

import numpy as np

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
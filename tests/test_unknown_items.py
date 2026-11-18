import json
import os
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from chest_scanner import paths
from chest_scanner.scanner import items as items_module
from chest_scanner.scanner.items import ItemsMixin


def _reader():
    reader = ItemsMixin.__new__(ItemsMixin)
    reader.items_db = {"items": []}
    reader.prompted_unknowns = set()
    reader.unknown_item_category = "uncategorized"
    reader._rebuild_indexes = lambda: None
    return reader


def test_failed_unknown_image_write_does_not_add_broken_database_entry(
        tmp_path, monkeypatch):
    monkeypatch.setattr(items_module, "absolute_path",
                        lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(items_module, "save_json", lambda *_: None)
    monkeypatch.setattr(paths, "UNKNOWN_QUEUE_FILE", str(tmp_path / "unknown_items.json"))
    monkeypatch.setattr(items_module.cv2, "imwrite", lambda *_: False)
    reader = _reader()
    image = np.full((24, 24, 3), 127, dtype=np.uint8)

    result = reader.add_unknown_item(image)

    assert result.startswith("unknown_")
    assert reader.items_db["items"] == []
    assert result in reader.prompted_unknowns
    assert not list(tmp_path.rglob("*.png"))


def test_unknown_item_is_added_only_after_image_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(items_module, "absolute_path",
                        lambda rel: str(tmp_path / rel))
    saved = []
    monkeypatch.setattr(items_module, "save_json", lambda _path, db, **_kwargs: saved.append(db))
    monkeypatch.setattr(paths, "UNKNOWN_QUEUE_FILE", str(tmp_path / "unknown_items.json"))
    reader = _reader()
    image = np.full((24, 24, 3), 127, dtype=np.uint8)

    result = reader.add_unknown_item(image)

    assert result.startswith("unknown_")
    assert reader.items_db["items"] == []
    assert (tmp_path / "unknown_items" / f"{result}.png").is_file()
    assert saved and saved[0]["items"][0]["id"] == result

def test_unknown_queue_deduplicates_and_counts_occurrences(tmp_path, monkeypatch):
    monkeypatch.setattr(items_module, "absolute_path",
                        lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(paths, "UNKNOWN_QUEUE_FILE", str(tmp_path / "unknown_items.json"))
    reader = _reader()
    image = np.full((24, 24, 3), 127, dtype=np.uint8)

    first = reader.add_unknown_item(image)
    second = reader.add_unknown_item(image)

    import json
    queue = json.loads((tmp_path / "unknown_items.json").read_text(encoding="utf-8"))
    assert first == second
    assert len(queue["items"]) == 1
    assert queue["items"][0]["occurrences"] == 2
    assert reader.items_db["items"] == []


def test_concurrent_unknown_occurrences_are_not_lost(tmp_path, monkeypatch):
    monkeypatch.setattr(items_module, "absolute_path",
                        lambda rel: str(tmp_path / rel))
    queue_path = tmp_path / "unknown_items.json"
    monkeypatch.setattr(paths, "UNKNOWN_QUEUE_FILE", str(queue_path))
    reader = _reader()
    image = np.full((24, 24, 3), 127, dtype=np.uint8)
    item_id = reader.add_unknown_item(image)

    original_load = items_module.load_unknown_queue

    def delayed_load(path):
        value = original_load(path)
        time.sleep(0.05)
        return value

    monkeypatch.setattr(items_module, "load_unknown_queue", delayed_load)
    start = threading.Barrier(3)
    errors = []

    def add_one():
        try:
            start.wait(timeout=5)
            reader.add_unknown_item(image)
        except Exception as exc:
            errors.append(exc)

    workers = [threading.Thread(target=add_one) for _ in range(2)]
    for worker in workers:
        worker.start()
    start.wait(timeout=5)
    for worker in workers:
        worker.join(timeout=5)

    assert not errors
    assert all(not worker.is_alive() for worker in workers)
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    assert len(queue["items"]) == 1
    assert queue["items"][0]["id"] == item_id
    assert queue["items"][0]["occurrences"] == 3


@pytest.mark.parametrize("bad_image", [
    None,
    np.array([], dtype=np.uint8),
    np.zeros((20,), dtype=np.uint8),
    np.zeros((20, 20), dtype=np.uint8),
    np.zeros((20, 20, 4), dtype=np.uint8),
    np.zeros((20, 20, 3), dtype=np.float32),
    np.full((20, 20, 3), np.nan, dtype=np.float32),
])
def test_invalid_icon_is_not_added_to_unknown_queue(bad_image):
    reader = _reader()
    assert reader.add_unknown_item(bad_image) == "unknown_empty"
    assert reader.prompted_unknowns == set()


@pytest.mark.parametrize("roi", [
    [-0.1, 0.1, 0.5, 0.5],
    [0.7, 0.1, 0.5, 0.5],
    [0.1, 0.7, 0.5, 0.5],
    [0.1, 0.1, 0.0, 0.5],
    [0.1, 0.1, float("nan"), 0.5],
])
def test_icon_extraction_rejects_invalid_or_clipped_roi(roi):
    reader = _reader()
    reader.config = {"icon_roi_in_slot": roi}
    assert reader.extract_icon(np.zeros((64, 64, 3), dtype=np.uint8)) is None


def test_icon_extraction_accepts_roi_touching_slot_edge():
    reader = _reader()
    reader.config = {"icon_roi_in_slot": [0.5, 0.5, 0.5, 0.5]}
    icon = reader.extract_icon(np.zeros((64, 64, 3), dtype=np.uint8))
    assert icon is not None and icon.shape == (32, 32, 3)


@pytest.mark.parametrize("bad_image", [
    None, np.zeros((20,), dtype=np.uint8), np.zeros((20, 20, 4), dtype=np.uint8),
])
def test_item_detection_rejects_malformed_icon_without_processing(bad_image):
    result = ItemsMixin.detect_item(_reader(), bad_image)
    assert result == (None, True, float("inf"), "uncategorized")


def test_unknown_queue_recovers_backup_and_preserves_corrupt_source(tmp_path):
    path = tmp_path / "unknown_items.json"
    expected = {"items": [{
        "id": "unknown_abc", "image": "unknown_items/unknown_abc.png",
        "occurrences": 4,
    }]}
    path.write_text('{"items": [', encoding="utf-8")
    (tmp_path / "unknown_items.json.backup").write_text(
        json.dumps(expected), encoding="utf-8")

    queue = items_module.load_unknown_queue(str(path))

    assert queue == expected
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert len(list(tmp_path.glob("unknown_items.json.corrupt-*"))) == 1


def test_unknown_queue_without_backup_quarantines_corrupt_data(tmp_path):
    path = tmp_path / "unknown_items.json"
    path.write_text('{broken', encoding="utf-8")

    assert items_module.load_unknown_queue(str(path)) == {"items": []}
    assert not path.exists()
    assert [p.read_text(encoding="utf-8") for p in tmp_path.glob(
        "unknown_items.json.corrupt-*")] == ['{broken']


def test_process_crash_after_unknown_image_commit_is_recoverable(
        tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    unknown_dir = profile / "unknown_items"
    unknown_dir.mkdir(parents=True)
    queue_path = profile / "unknown_items.json"
    script = "\n".join([
        "import os, sys",
        "import numpy as np",
        "from chest_scanner import paths",
        "paths.PROFILE_DIR = sys.argv[1]",
        "paths.UNKNOWN_DIR = os.path.join(sys.argv[1], 'unknown_items')",
        "paths.UNKNOWN_QUEUE_FILE = os.path.join(sys.argv[1], 'unknown_items.json')",
        "from chest_scanner.scanner import items",
        "items.save_json = lambda *args, **kwargs: os._exit(77)",
        "reader = items.ItemsMixin.__new__(items.ItemsMixin)",
        "reader.prompted_unknowns = set()",
        "reader.add_unknown_item(np.full((24, 24, 3), 127, dtype=np.uint8))",
    ])
    result = subprocess.run(
        [sys.executable, "-c", script, str(profile)],
        cwd=os.getcwd(), capture_output=True, text=True, timeout=20)
    assert result.returncode == 77, (result.stdout, result.stderr)

    images = list(unknown_dir.glob("unknown_*.png"))
    assert len(images) == 1
    assert not queue_path.exists()

    monkeypatch.setattr(paths, "PROFILE_DIR", str(profile))
    monkeypatch.setattr(paths, "UNKNOWN_DIR", str(unknown_dir))
    monkeypatch.setattr(paths, "UNKNOWN_QUEUE_FILE", str(queue_path))
    reader = _reader()
    result_id = reader.add_unknown_item(
        np.full((24, 24, 3), 127, dtype=np.uint8))

    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    assert result_id == images[0].stem
    assert len(queue["items"]) == 1
    assert queue["items"][0]["id"] == result_id
    assert len(list(unknown_dir.glob("unknown_*.png"))) == 1

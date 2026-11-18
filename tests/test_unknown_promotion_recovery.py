"""Retry safety for promoting an unknown icon into the item database."""
import json
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from chest_scanner import items as items_module
from chest_scanner import paths
from chest_scanner.gui import item_db_view
from chest_scanner.gui.item_db_view import UnknownItemsView


def test_promotion_retry_after_database_commit_does_not_duplicate_icon(
        tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile = tmp_path / "profile"
    unknown_dir = profile / "unknown_items"
    item_dir = profile / "items"
    unknown_dir.mkdir(parents=True)
    item_dir.mkdir()
    queue_path = profile / "unknown_items.json"
    source_rel = "unknown_items/unknown_abc.png"
    source = unknown_dir / "unknown_abc.png"
    source.write_bytes(b"stable-icon-content")
    queue = {"items": [{
        "id": "unknown_abc", "image": source_rel, "occurrences": 3,
        "first_seen": "first", "last_seen": "last",
    }]}
    queue_path.write_text(json.dumps(queue), encoding="utf-8")

    monkeypatch.setattr(paths, "UNKNOWN_QUEUE_FILE", str(queue_path))
    monkeypatch.setattr(item_db_view, "absolute_path",
                        lambda rel: str(profile / rel))
    monkeypatch.setattr(items_module, "absolute_path",
                        lambda rel: str(profile / rel))
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args: None)

    reader = SimpleNamespace(items_db={"items": []})
    saved_databases = []

    def save_db(db):
        reader.items_db = db
        saved_databases.append(json.loads(json.dumps(db)))

    parent = QWidget()
    view = UnknownItemsView(parent, reader, save_db)
    view.name_edit.setText("Stone")
    original_save = item_db_view.save_json
    fail_queue_commit = {"pending": True}

    def fail_once(path, data, backup=False):
        if str(path) == str(queue_path) and fail_queue_commit["pending"]:
            fail_queue_commit["pending"] = False
            raise OSError("simulated process interruption after database commit")
        return original_save(path, data, backup=backup)

    monkeypatch.setattr(item_db_view, "save_json", fail_once)
    view._promote_current()

    assert len(reader.items_db["items"]) == 1
    assert len(reader.items_db["items"][0]["images"]) == 1
    assert json.loads(queue_path.read_text(encoding="utf-8")) == queue
    view.close()
    parent.close()

    parent_retry = QWidget()
    retry = UnknownItemsView(parent_retry, reader, save_db)
    retry.name_edit.setText("Stone")
    retry._promote_current()

    item = reader.items_db["items"][0]
    assert len(item["images"]) == 1
    assert len(list(item_dir.glob("*.png"))) == 1
    assert json.loads(queue_path.read_text(encoding="utf-8")) == {"items": []}
    assert len(saved_databases) == 2
    retry.close()
    parent_retry.close()
    app.processEvents()

"""Tests for safe item duplication."""
import pytest
from PySide6.QtCore import QSettings as QtQSettings
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from chest_scanner.gui import item_db_view
from chest_scanner.gui.item_db_view import ItemDatabaseView


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_duplicate_item_copies_images_and_preserves_original(
        qapp, tmp_path, monkeypatch):
    source = tmp_path / "images" / "original.png"
    source.parent.mkdir()
    source.write_bytes(b"image-bytes")
    db = {
        "items": [{
            "id": "original-id",
            "name": "Diamond",
            "stack_size": 64,
            "category": "materials",
            "enabled": True,
            "images": ["images/original.png"],
            "image": "images/original.png",
        }]
    }
    original = db["items"][0]
    settings_file = tmp_path / "item-db.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    view.show()
    view.table.selectRow(0)
    view.table.setFocus()
    view._ask_text = lambda *_args: "Diamond copy"
    messages = []
    view._after_change = lambda message, **_kwargs: messages.append(message)

    monkeypatch.setattr(
        item_db_view, "absolute_path",
        lambda rel: str(tmp_path / rel))

    def next_path(name, extension):
        return f"images/{name.replace(' ', '_')}{extension}"

    monkeypatch.setattr(item_db_view, "next_image_path", next_path)
    qapp.processEvents()
    QTest.keyClick(view.table, Qt.Key_D, Qt.ControlModifier)
    qapp.processEvents()

    assert len(db["items"]) == 2
    duplicate = db["items"][1]
    assert duplicate["id"] != original["id"]
    assert duplicate["name"] == "Diamond copy"
    assert duplicate["stack_size"] == original["stack_size"]
    assert duplicate["category"] == original["category"]
    assert duplicate["enabled"] is original["enabled"]
    assert duplicate["images"] != original["images"]
    assert duplicate["image"] == duplicate["images"][0]
    assert (tmp_path / duplicate["image"]).read_bytes() == b"image-bytes"
    assert original["images"] == ["images/original.png"]
    assert messages and "Diamond copy" in messages[0]
    view.close()


def test_duplicate_item_rejects_existing_name(qapp, tmp_path, monkeypatch):
    db = {
        "items": [{
            "id": "original-id",
            "name": "Diamond",
            "stack_size": 64,
            "category": "materials",
            "enabled": True,
            "images": [],
            "image": "",
        }]
    }
    settings_file = tmp_path / "item-db.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    view._selected_item = lambda: db["items"][0]
    view._ask_text = lambda *_args: "Diamond"
    warnings = []
    monkeypatch.setattr(
        item_db_view.QMessageBox, "warning",
        lambda *_args: warnings.append(True))
    view._duplicate_item()
    assert len(db["items"]) == 1
    assert warnings == [True]
    view.close()



def test_add_item_rolls_back_partial_photo_import(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-add-rollback.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": []}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    view._ask_text = lambda *_args: "New item"
    monkeypatch.setattr(item_db_view, "_ask_stack_size", lambda *_args: 64)
    view._choose_category = lambda: "blocks"
    view._ask_image_files = lambda *_args: ["first.jpg", "second.webp"]
    destinations = iter(("items/first.png", "items/second.png"))
    monkeypatch.setattr(
        item_db_view, "next_image_path", lambda *_args: next(destinations))
    monkeypatch.setattr(
        item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))

    def failing_copy(_source, relative):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"partial image")
        if relative.endswith("second.png"):
            raise OSError("image conversion failed")

    monkeypatch.setattr(item_db_view, "copy_image_as_png", failing_copy)
    errors = []
    monkeypatch.setattr(
        item_db_view.QMessageBox, "critical",
        lambda *_args: errors.append(True))

    view._add_item()

    assert db["items"] == []
    assert not (tmp_path / "items/first.png").exists()
    assert not (tmp_path / "items/second.png").exists()
    assert errors == [True]
    view.close()


def test_add_item_imports_multiple_photos_and_sets_primary(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-add-success.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": []}
    saved = []
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db,
        save_db=lambda value: saved.append(value["items"][0].copy()))
    view._ask_text = lambda *_args: "New item"
    monkeypatch.setattr(item_db_view, "_ask_stack_size", lambda *_args: 16)
    view._choose_category = lambda: "food"
    view._ask_image_files = lambda *_args: ["first.webp", "second.bmp"]
    destinations = iter(("items/first.png", "items/second.png"))
    monkeypatch.setattr(
        item_db_view, "next_image_path", lambda *_args: next(destinations))
    monkeypatch.setattr(
        item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))

    def copy_image(_source, relative):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"converted png")

    monkeypatch.setattr(item_db_view, "copy_image_as_png", copy_image)

    view._add_item()

    assert len(db["items"]) == 1
    item = db["items"][0]
    assert item["name"] == "New item"
    assert item["stack_size"] == 16
    assert item["category"] == "food"
    assert item["images"] == ["items/first.png", "items/second.png"]
    assert item["image"] == "items/first.png"
    assert all((tmp_path / path).is_file() for path in item["images"])
    assert saved and saved[0]["images"] == item["images"]
    view.close()

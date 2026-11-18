"""Persistence tests for item database search and filters."""
from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QMimeData, QModelIndex, Qt, QUrl
from PySide6.QtCore import QSettings as QtQSettings
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from chest_scanner import items as items_module
from chest_scanner.commons import load_json, save_json
from chest_scanner.gui import item_db_view
from chest_scanner.gui.item_db_view import ItemDatabaseView


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_refresh_allocates_table_rows_in_one_batch(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-batch-refresh.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "one", "name": "Diamond", "category": "blocks", "images": []},
        {"id": "two", "name": "Apple", "category": "food", "images": []},
        {"id": "three", "name": "Dirt", "category": "blocks", "images": []},
    ]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)

    def unexpected_insert_row(*_args, **_kwargs):
        pytest.fail("refresh_list must allocate rows in one setRowCount call")

    monkeypatch.setattr(item_db_view.QTableWidget, "insertRow", unexpected_insert_row)
    view.search_edit.setText("iam")
    view._filter_changed()

    assert view.table.rowCount() == 1
    assert {view.table.item(row, 0).text()
            for row in range(view.table.rowCount())} == {"Diamond"}
    assert view.result_label.text() == "1 / 3"
    view.close()


def test_typing_search_does_not_recount_database_filter_totals(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-search-counts.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "one", "name": "Diamond", "category": "blocks", "images": []},
        {"id": "two", "name": "Apple", "category": "food", "images": []},
    ]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    original_counts = view._update_filter_counts
    calls = []

    def track_counts(items):
        calls.append(len(items))
        original_counts(items)

    monkeypatch.setattr(view, "_update_filter_counts", track_counts)
    view.search_edit.setText("diam")

    assert calls == []
    assert view.table.rowCount() == 1
    assert view.table.item(0, 0).text() == "Diamond"
    blocks_index = view.cat_combo.findData("blocks")
    assert view.cat_combo.itemText(blocks_index).endswith("(1)")

    # A normal data refresh still recalculates totals.
    view.refresh_list()
    assert calls == [2]
    view.close()


def test_refresh_restores_table_state_after_population_error(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-refresh-error.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [{
        "id": "one", "name": "Diamond", "category": "blocks",
        "images": [],
    }]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    assert view.table.isSortingEnabled()
    assert view.table.updatesEnabled()
    db["items"][0]["images"] = ["broken.png"]

    def fail_thumbnail(_path):
        raise RuntimeError("simulated thumbnail failure")

    monkeypatch.setattr(item_db_view, "_thumb_pixmap", fail_thumbnail)
    with pytest.raises(RuntimeError, match="simulated thumbnail failure"):
        view.refresh_list()

    assert view.table.isSortingEnabled()
    assert view.table.updatesEnabled()
    view.close()


def test_filter_count_update_restores_signal_state_after_error(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-filter-error.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [{
        "id": "one", "name": "Diamond", "category": "blocks",
        "images": [],
    }]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    original = item_db_view.QComboBox.setItemText
    view.cat_combo.blockSignals(True)

    def fail_item_text(combo, index, value):
        if index == 0:
            raise RuntimeError("simulated combo failure")
        return original(combo, index, value)

    monkeypatch.setattr(item_db_view.QComboBox, "setItemText", fail_item_text)
    with pytest.raises(RuntimeError, match="simulated combo failure"):
        view._update_filter_counts(db["items"])

    assert view.cat_combo.signalsBlocked()
    assert not view.status_combo.signalsBlocked()
    view.close()


def test_search_and_filters_restore_on_next_open(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [{
        "id": "one",
        "name": "Diamond",
        "stack_size": 64,
        "category": "blocks",
        "enabled": True,
        "images": [],
    }]}

    first = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    first.search_edit.setText("Diamond")
    first.cat_combo.setCurrentIndex(2)
    first.status_combo.setCurrentIndex(1)
    first.close()

    second = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    assert second.search_edit.text() == "Diamond"
    assert second.cat_combo.currentIndex() == 2
    assert second.status_combo.currentData() == "enabled"
    second.close()


def test_failed_save_restores_database_and_keeps_images(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    image = tmp_path / "kept.png"
    image.write_bytes(b"original image")
    original = {"items": [{"id": "one", "name": "Diamond", "images": ["kept.png"]}]}
    db = {"items": [dict(original["items"][0])]}

    def fail_save(_db):
        raise OSError("simulated disk error")

    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=fail_save)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda _path: str(image))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *_a, **_k: None))
    db["items"].clear()

    assert view._after_change(delete_files=("kept.png",)) is False
    assert db == original
    assert image.read_bytes() == b"original image"
    view.close()


def test_successful_save_deletes_deferred_images(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    image = tmp_path / "remove.png"
    image.write_bytes(b"obsolete image")
    db = {"items": [{"id": "one", "name": "Diamond", "images": ["remove.png"]}]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda _path: str(image))
    db["items"].clear()

    assert view._after_change(delete_files=("remove.png",)) is True
    assert not image.exists()
    view.close()


def test_undo_deleted_item_restores_its_image(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    image = tmp_path / "diamond.png"
    image.write_bytes(b"diamond image")
    db = {"items": [{"id": "one", "name": "Diamond", "images": ["diamond.png"]}]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))

    db["items"].clear()
    assert view._after_change(delete_files=("diamond.png",)) is True
    assert not image.exists()
    assert view.undo_button.isEnabled()

    view.undo_button.click()
    assert db["items"][0]["name"] == "Diamond"
    assert image.read_bytes() == b"diamond image"
    assert not view.undo_button.isEnabled()
    view.close()


def test_undo_added_photo_removes_new_file(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    image = tmp_path / "new.png"
    image.write_bytes(b"new image")
    db = {"items": []}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))

    db["items"].append({"id": "new", "name": "Emerald", "images": ["new.png"]})
    assert view._after_change(rollback_files=("new.png",)) is True
    assert image.exists()
    view._undo_last_change()
    assert db["items"] == []
    assert not image.exists()
    view.close()


def test_undo_photo_rename_restores_original_path(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    old_image = tmp_path / "old.png"
    new_image = tmp_path / "new.png"
    old_image.write_bytes(b"original image")
    db = {"items": [{"id": "one", "name": "Diamond", "images": ["old.png"]}]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))

    old_image.rename(new_image)
    db["items"][0]["images"] = ["new.png"]
    assert view._after_change(restore_files=(("new.png", "old.png"),)) is True
    view._undo_last_change()
    assert db["items"][0]["images"] == ["old.png"]
    assert old_image.read_bytes() == b"original image"
    assert not new_image.exists()
    view.close()


def test_failed_undo_keeps_current_database_and_file_state(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    image = tmp_path / "kept-current.png"
    image.write_bytes(b"current image")
    db = {"items": [{"id": "one", "name": "Diamond", "images": ["kept-current.png"]}]}
    save_calls = []

    def save_db(_db):
        save_calls.append(True)
        if len(save_calls) == 2:
            raise OSError("simulated undo write failure")

    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=save_db)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *_a, **_k: None))
    db["items"].clear()
    assert view._after_change(delete_files=("kept-current.png",)) is True
    assert not image.exists()

    view._undo_last_change()
    assert db["items"] == []
    assert not image.exists()
    assert view.undo_button.isEnabled()
    view.close()



def test_table_sorting_supports_text_and_numeric_columns(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-sort.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "two", "name": "Beta", "stack_size": 2,
         "category": "blocks", "enabled": True, "images": ["a.png", "b.png"]},
        {"id": "sixteen", "name": "Alpha", "stack_size": 16,
         "category": "food", "enabled": True, "images": ["a.png"]},
        {"id": "sixty-four", "name": "Gamma", "stack_size": 64,
         "category": "ores", "enabled": False, "images": []},
    ]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)

    assert view.table.isSortingEnabled()
    assert "??????????? ?? ?????????" in view.table.horizontalHeaderItem(1).toolTip()

    view.table.sortItems(0, Qt.DescendingOrder)
    assert [view.table.item(row, 0).text() for row in range(3)] == [
        "Gamma", "Beta", "Alpha"]

    view.table.sortItems(2, Qt.AscendingOrder)
    assert [view.table.item(row, 2).text() for row in range(3)] == [
        "2", "16", "64"]

    view.table.sortItems(3, Qt.DescendingOrder)
    assert [view.table.item(row, 3).text() for row in range(3)] == [
        "2", "1", "0"]
    view.close()


def test_refresh_keeps_selected_item_after_sorting(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-selection.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "beta", "name": "Beta", "stack_size": 64,
         "category": "blocks", "enabled": True, "images": []},
        {"id": "alpha", "name": "Alpha", "stack_size": 16,
         "category": "food", "enabled": True, "images": []},
    ]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    view.table.selectRow(1)
    assert view._selected_item()["id"] == "beta"

    view.table.selectionModel().setCurrentIndex(
        view.table.model().index(1, 0), item_db_view.QItemSelectionModel.NoUpdate)
    view.table.sortItems(0, Qt.DescendingOrder)
    view.refresh_list()

    assert view._selected_item()["id"] == "beta"
    current = view.table.currentItem()
    assert current is not None
    assert current.data(Qt.UserRole) == "beta"

    activated = []
    view._edit_item = lambda: activated.append("edit")
    view.show()
    qapp.processEvents()
    view.table.setFocus()
    qapp.processEvents()
    QTest.keyClick(view.table, Qt.Key_Return)
    qapp.processEvents()
    assert activated == ["edit"]

    view.table.setFocus()
    qapp.processEvents()
    QTest.keyClick(view.table, Qt.Key_Escape)
    qapp.processEvents()
    assert view.table.selectionModel().selectedRows() == []
    assert view._selected_item() is None
    view.close()



def test_reset_filters_clears_search_category_and_status(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-reset.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "diamond", "name": "Diamond", "category": "blocks",
         "enabled": True, "images": []},
        {"id": "apple", "name": "Apple", "category": "food",
         "enabled": False, "images": []},
    ]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)

    view.search_edit.setText("Diamond")
    view.cat_combo.setCurrentIndex(view.cat_combo.findData("blocks"))
    view.status_combo.setCurrentIndex(view.status_combo.findData("enabled"))
    assert view.table.rowCount() == 1
    assert view.reset_filters_button.isEnabled()

    view.reset_filters_button.click()

    assert view.search_edit.text() == ""
    assert view.cat_combo.currentIndex() == 0
    assert view.status_combo.currentData() == "all"
    assert view.table.rowCount() == 2
    assert not view.reset_filters_button.isEnabled()
    settings = QtQSettings(str(settings_file), QtQSettings.IniFormat)
    settings.sync()
    assert settings.value("item_database/search") == ""
    assert settings.value("item_database/category") == ""
    assert settings.value("item_database/status") == "all"
    view.close()


def test_reset_filters_is_safe_when_already_clear(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-reset-empty.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "one", "name": "One", "category": "blocks", "images": []},
    ]}
    view = ItemDatabaseView(
        None, read_only=True, get_db=lambda: db)

    assert not view.reset_filters_button.isEnabled()
    view.reset_filters_button.click()
    assert view.search_edit.text() == ""
    assert view.cat_combo.currentIndex() == 0
    assert view.status_combo.currentData() == "all"
    assert view.table.rowCount() == 1
    assert not view.reset_filters_button.isEnabled()
    view.close()


def test_reset_button_tracks_each_filter(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-reset-button.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    view = ItemDatabaseView(
        None, read_only=True, get_db=lambda: {"items": []})

    assert not view.reset_filters_button.isEnabled()
    view.search_edit.setText("stone")
    assert view.reset_filters_button.isEnabled()
    view.search_edit.clear()
    assert not view.reset_filters_button.isEnabled()
    view.cat_combo.setCurrentIndex(1)
    assert view.reset_filters_button.isEnabled()
    view.cat_combo.setCurrentIndex(0)
    assert not view.reset_filters_button.isEnabled()
    view.status_combo.setCurrentIndex(1)
    assert view.reset_filters_button.isEnabled()
    view.status_combo.setCurrentIndex(0)
    assert not view.reset_filters_button.isEnabled()
    view.close()



def test_filter_dropdowns_show_live_category_and_status_counts(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-counts.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "b1", "name": "Stone", "category": "blocks",
         "enabled": True, "images": []},
        {"id": "b2", "name": "Dirt", "category": "blocks",
         "enabled": False, "images": []},
        {"id": "f1", "name": "Apple", "category": "food",
         "enabled": True, "images": []},
    ]}
    view = ItemDatabaseView(
        None, read_only=True, get_db=lambda: db)

    assert view.cat_combo.itemText(0) == "Все категории (3)"
    assert view.cat_combo.itemText(view.cat_combo.findData("blocks")) == "Блоки (2)"
    assert view.cat_combo.itemText(view.cat_combo.findData("food")) == "Еда (1)"
    assert view.status_combo.itemText(0) == "Все состояния (3)"
    assert view.status_combo.itemText(1) == "Вкл. (2)"
    assert view.status_combo.itemText(2) == "Выкл. (1)"

    view.cat_combo.setCurrentIndex(view.cat_combo.findData("blocks"))
    view.status_combo.setCurrentIndex(view.status_combo.findData("disabled"))
    db["items"].append({
        "id": "b3", "name": "Granite", "category": "blocks",
        "enabled": True, "images": [],
    })
    view.refresh_list()

    assert view.cat_combo.currentData() == "blocks"
    assert view.status_combo.currentData() == "disabled"
    assert view.cat_combo.itemText(view.cat_combo.findData("blocks")) == "Блоки (3)"
    assert view.status_combo.itemText(1) == "Вкл. (3)"
    assert view.status_combo.itemText(2) == "Выкл. (1)"
    assert view.table.rowCount() == 1
    view.close()




def test_search_matches_item_name_case_insensitively(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-search-case.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "diamond", "name": "Diamond", "category": "ores", "images": []},
        {"id": "apple", "name": "Apple", "category": "food", "images": []},
    ]}
    view = ItemDatabaseView(None, read_only=True, get_db=lambda: db)

    view.search_edit.setText("dIaMoNd")
    assert [view.table.item(row, 0).text()
            for row in range(view.table.rowCount())] == ["Diamond"]
    view.search_edit.setText("aPpLe")
    assert [view.table.item(row, 0).text()
            for row in range(view.table.rowCount())] == ["Apple"]
    view.close()


def test_search_matches_category_display_name_and_key(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-search-category.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "stone", "name": "Stone", "category": "blocks", "images": []},
        {"id": "apple", "name": "Apple", "category": "food", "images": []},
        {"id": "iron", "name": "Iron", "category": "ores", "images": []},
    ]}
    view = ItemDatabaseView(None, read_only=True, get_db=lambda: db)

    view.search_edit.setText("\u0431\u041b\u041e\u041a\u0438")
    assert {view.table.item(row, 0).data(Qt.UserRole)
            for row in range(view.table.rowCount())} == {"stone"}
    view.search_edit.setText("FOOD")
    assert {view.table.item(row, 0).data(Qt.UserRole)
            for row in range(view.table.rowCount())} == {"apple"}
    view.close()


def test_category_search_combines_with_filters_without_changing_counts(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-search-combined.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "stone", "name": "Stone", "category": "blocks",
         "enabled": True, "images": []},
        {"id": "dirt", "name": "Dirt", "category": "blocks",
         "enabled": False, "images": []},
        {"id": "apple", "name": "Apple", "category": "food",
         "enabled": True, "images": []},
    ]}
    view = ItemDatabaseView(None, read_only=True, get_db=lambda: db)

    view.search_edit.setText("\u0431\u043b\u043e\u043a\u0438")
    view.status_combo.setCurrentIndex(view.status_combo.findData("enabled"))
    assert [view.table.item(row, 0).data(Qt.UserRole)
            for row in range(view.table.rowCount())] == ["stone"]
    assert view.cat_combo.itemText(view.cat_combo.findData("blocks")) == (
        "\u0411\u043b\u043e\u043a\u0438 (2)")
    assert view.status_combo.itemText(0) == (
        "\u0412\u0441\u0435 \u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u044f (3)")
    assert view.result_label.text() == "1 / 3"
    view.close()


def test_search_does_not_match_unrelated_item_fields(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-search-fields.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [{
        "id": "stone", "name": "Stone", "category": "blocks",
        "stack_size": 64, "enabled": True, "images": [],
        "description": "secretword",
    }]}
    view = ItemDatabaseView(None, read_only=True, get_db=lambda: db)

    view.search_edit.setText("secretword")
    assert view.table.rowCount() == 0
    view.close()



def test_text_columns_sort_without_case_sensitivity(qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-sort-case.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "zulu", "name": "zulu", "category": "blocks",
         "enabled": True, "images": []},
        {"id": "alpha-upper", "name": "ALPHA", "category": "food",
         "enabled": True, "images": []},
        {"id": "alpha-lower", "name": "alpha", "category": "ores",
         "enabled": False, "images": []},
        {"id": "beta", "name": "Beta", "category": "redstone",
         "enabled": False, "images": []},
    ]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)

    view.table.sortItems(0, Qt.AscendingOrder)
    assert [view.table.item(row, 0).text()
            for row in range(view.table.rowCount())] == [
                "ALPHA", "alpha", "Beta", "zulu"]

    view.table.sortItems(1, Qt.AscendingOrder)
    categories = [view.table.item(row, 1).text()
                  for row in range(view.table.rowCount())]
    assert categories == sorted(categories, key=str.casefold)

    view.table.sortItems(4, Qt.AscendingOrder)
    statuses = [view.table.item(row, 4).text()
                for row in range(view.table.rowCount())]
    assert statuses == sorted(statuses, key=str.casefold)
    view.close()


def test_table_sort_settings_restore_after_reopening_and_refresh(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-sort-persistence.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "two", "name": "Beta", "stack_size": 2,
         "category": "blocks", "enabled": True, "images": []},
        {"id": "sixteen", "name": "Alpha", "stack_size": 16,
         "category": "food", "enabled": True, "images": []},
        {"id": "sixty-four", "name": "Gamma", "stack_size": 64,
         "category": "ores", "enabled": False, "images": []},
    ]}

    first = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    first.table.sortItems(2, Qt.DescendingOrder)
    first.refresh_list()

    settings = QtQSettings(str(settings_file), QtQSettings.IniFormat)
    settings.sync()
    assert settings.value("item_database/sort_column", type=int) == 2
    assert settings.value("item_database/sort_order", type=int) == Qt.DescendingOrder.value
    assert [first.table.item(row, 2).text() for row in range(3)] == [
        "64", "16", "2"]
    first.close()

    second = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    header = second.table.horizontalHeader()
    assert header.sortIndicatorSection() == 2
    assert header.sortIndicatorOrder() == Qt.DescendingOrder
    assert [second.table.item(row, 2).text() for row in range(3)] == [
        "64", "16", "2"]
    second.close()


def test_table_sort_settings_default_to_name_ascending(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-sort-default.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [
        {"id": "b", "name": "Beta", "images": []},
        {"id": "a", "name": "Alpha", "images": []},
    ]}

    view = ItemDatabaseView(None, read_only=True, get_db=lambda: db)

    header = view.table.horizontalHeader()
    assert header.sortIndicatorSection() == 0
    assert header.sortIndicatorOrder() == Qt.AscendingOrder
    assert [view.table.item(row, 0).text() for row in range(2)] == [
        "Alpha", "Beta"]
    view.close()


def test_photo_list_shows_thumbnails_and_switches_main_preview(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-preview.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))

    def fake_thumbnail(_path, size=32):
        return QPixmap(size, size)

    monkeypatch.setattr(item_db_view, "_thumb_pixmap", fake_thumbnail)
    monkeypatch.setattr(
        item_db_view, "_photo_problem", lambda _path, **_kwargs: None)
    db = {"items": [{
        "id": "photos",
        "name": "Test item",
        "stack_size": 64,
        "category": "blocks",
        "enabled": True,
        "images": ["first.png", "second.png"],
    }]}
    view = ItemDatabaseView(None, read_only=True, get_db=lambda: db)
    view.table.selectRow(0)

    assert view.photo_list.count() == 2
    assert view.photo_list.item(0).text() == "first.png"
    assert view.photo_list.item(1).text() == "second.png"
    assert not view.photo_list.item(0).icon().isNull()
    assert view.preview.toolTip() == "first.png"

    view.photo_list.setCurrentRow(1)
    assert view.preview.toolTip() == "second.png"
    assert not view.preview.pixmap().isNull()
    view.close()


def test_large_photo_viewer_navigates_and_preserves_aspect_ratio(
        qapp, tmp_path, monkeypatch):
    first = tmp_path / "wide.png"
    second = tmp_path / "tall.png"
    QPixmap(800, 400).save(str(first))
    QPixmap(300, 600).save(str(second))
    monkeypatch.setattr(item_db_view, "absolute_path", lambda path: str(path))

    viewer = item_db_view._PhotoViewerDialog([str(first), str(second)], 0)
    qapp.processEvents()
    assert viewer.counter_label.text() == "1 / 2"
    assert viewer.previous_button.isEnabled() is False
    assert viewer.next_button.isEnabled() is True
    first_size = viewer.image_label.pixmap().size()
    assert first_size.width() / first_size.height() == pytest.approx(2.0)

    viewer.next_button.click()
    qapp.processEvents()
    assert viewer.counter_label.text() == "2 / 2"
    assert viewer.previous_button.isEnabled() is True
    assert viewer.next_button.isEnabled() is False
    second_size = viewer.image_label.pixmap().size()
    assert second_size.width() / second_size.height() == pytest.approx(0.5)

    viewer.previous_button.click()
    assert viewer.counter_label.text() == "1 / 2"
    viewer.close()


def test_photo_list_double_click_opens_viewer_at_selected_photo(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-open.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [{
        "id": "photos", "name": "Test item", "images": ["first.png", "second.png"]
    }]}
    view = ItemDatabaseView(None, read_only=True, get_db=lambda: db)
    view.table.selectRow(0)
    view.photo_list.setCurrentRow(1)
    opened = []

    class FakeViewer:
        def __init__(self, paths, start_index, parent):
            opened.append((paths, start_index, parent))

        def exec(self):
            return 0

    monkeypatch.setattr(item_db_view, "_PhotoViewerDialog", FakeViewer)
    view._open_photo_viewer(view.photo_list.item(1))
    assert opened == [(["first.png", "second.png"], 1, view)]
    view.close()


def test_photo_manager_shows_thumbnails_count_and_selected_preview(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-manager.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    QPixmap(800, 400).save(str(first))
    QPixmap(300, 600).save(str(second))
    db = {"items": [{
        "id": "photos", "name": "Test item",
        "images": [str(first), str(second)],
    }]}
    view = ItemDatabaseView(None, read_only=False, get_db=lambda: db,
                            save_db=lambda _db: None)
    view.table.selectRow(0)
    dialogs = []

    def capture_dialog(dialog):
        dialogs.append(dialog)
        return 0

    monkeypatch.setattr(item_db_view.QDialog, "exec", capture_dialog)
    view._manage_photos()
    assert len(dialogs) == 1
    dialog = dialogs[0]
    photo_list = dialog.findChild(item_db_view.QListWidget)
    labels = dialog.findChildren(item_db_view.QLabel)
    assert photo_list.count() == 2
    assert photo_list.dragEnabled()
    assert photo_list.acceptDrops()
    assert photo_list.showDropIndicator()
    assert photo_list.dragDropMode() == item_db_view.QAbstractItemView.InternalMove
    assert photo_list.defaultDropAction() == item_db_view.Qt.MoveAction
    assert "??????????" in photo_list.toolTip()
    assert photo_list.item(0).text().startswith("\u2605 \u0413\u043b\u0430\u0432\u043d\u0430\u044f")
    assert not photo_list.item(0).icon().isNull()
    assert photo_list.item(0).data(Qt.UserRole + 1) is True
    assert photo_list.item(1).data(Qt.UserRole + 1) is False
    assert photo_list.item(0).font().bold()
    assert photo_list.item(0).foreground().color() == item_db_view.QColor(
        view.pal["ACCENT_HOVER"])
    assert any("??????????: 2" in label.text() for label in labels)
    primary_button = next(
        button for button in dialog.findChildren(item_db_view.QPushButton)
        if button.text().startswith("\u2713"))
    assert not primary_button.isEnabled()
    preview = next(label for label in labels if label.minimumWidth() >= 280)
    assert not preview.pixmap().isNull()

    photo_list.setCurrentRow(1)
    qapp.processEvents()
    assert primary_button.isEnabled()
    assert primary_button.text().startswith("\u2605")
    assert not preview.pixmap().isNull()
    assert preview.pixmap().width() / preview.pixmap().height() == pytest.approx(0.5)

    primary_button.click()
    qapp.processEvents()
    assert db["items"][0]["images"] == [str(second), str(first)]
    assert db["items"][0]["image"] == str(second)
    assert photo_list.item(0).data(Qt.UserRole + 1) is True
    assert photo_list.item(0).font().bold()
    assert photo_list.item(0).text().startswith("\u2605 \u0413\u043b\u0430\u0432\u043d\u0430\u044f")
    assert not primary_button.isEnabled()
    assert primary_button.text().startswith("\u2713")
    view.close()

def test_photo_manager_drag_reorder_updates_primary_and_database(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-drag.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    third = tmp_path / "third.png"
    for path in (first, second, third):
        QPixmap(40, 40).save(str(path))
    original = [str(first), str(second), str(third)]
    db = {"items": [{
        "id": "photos", "name": "Test item", "images": list(original),
        "image": str(first),
    }]}
    saves = []
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db,
        save_db=lambda value: saves.append(list(value["items"][0]["images"])))
    view.table.selectRow(0)
    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    view._manage_photos()
    photo_list = dialogs[0].findChild(item_db_view.QListWidget)

    moved = photo_list.takeItem(2)
    photo_list.insertItem(0, moved)
    photo_list.setCurrentRow(0)
    photo_list.model().rowsMoved.emit(
        QModelIndex(), 2, 2, QModelIndex(), 0)
    qapp.processEvents()

    expected = [str(third), str(first), str(second)]
    assert db["items"][0]["images"] == expected
    assert db["items"][0]["image"] == str(third)
    assert saves == [expected]
    assert [photo_list.item(i).data(Qt.UserRole)
            for i in range(photo_list.count())] == expected
    assert photo_list.item(0).text().startswith("\u2605 \u0413\u043b\u0430\u0432\u043d\u0430\u044f")
    view.close()




def test_photo_manager_adds_multiple_images_and_updates_primary(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-add.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [{
        "id": "photos", "name": "Test item", "images": ["existing.png"],
        "image": "existing.png",
    }]}
    saves = []
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db,
        save_db=lambda value: saves.append(list(value["items"][0]["images"])))
    view.table.selectRow(0)
    sources = [str(tmp_path / "one.jpg"), str(tmp_path / "two.jpg")]
    destinations = iter(("photos/one.png", "photos/two.png"))
    monkeypatch.setattr(view, "_ask_image_files", lambda _title: sources)
    monkeypatch.setattr(item_db_view, "next_image_path",
                        lambda *_args: next(destinations))
    monkeypatch.setattr(item_db_view, "absolute_path",
                        lambda rel: str(tmp_path / rel))

    def copy_image(source, relative):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(Path(source).name, encoding="utf-8")

    monkeypatch.setattr(item_db_view, "copy_image_as_png", copy_image)
    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    view._manage_photos()
    dialog = dialogs[0]
    add_button = next(
        button for button in dialog.findChildren(item_db_view.QPushButton)
        if button.text().startswith("\u2795"))
    add_button.click()

    expected = ["existing.png", "photos/one.png", "photos/two.png"]
    assert db["items"][0]["images"] == expected
    assert db["items"][0]["image"] == "existing.png"
    assert saves == [expected]
    assert all((tmp_path / path).is_file() for path in expected[1:])
    assert any("3" in label.text() for label in dialog.findChildren(item_db_view.QLabel))
    view.close()


def test_photo_manager_rolls_back_partial_bulk_import(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-add-rollback.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [{
        "id": "photos", "name": "Test item", "images": ["existing.png"],
        "image": "existing.png",
    }]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    view.table.selectRow(0)
    monkeypatch.setattr(
        view, "_ask_image_files", lambda _title: ["one.jpg", "two.jpg"])
    destinations = iter(("photos/partial.png", "photos/fail.png"))
    monkeypatch.setattr(item_db_view, "next_image_path",
                        lambda *_args: next(destinations))
    monkeypatch.setattr(item_db_view, "absolute_path",
                        lambda rel: str(tmp_path / rel))

    def failing_copy(_source, relative):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"partial")
        if relative.endswith("fail.png"):
            raise OSError("conversion failed")

    monkeypatch.setattr(item_db_view, "copy_image_as_png", failing_copy)
    errors = []
    monkeypatch.setattr(
        item_db_view.QMessageBox, "critical",
        lambda *_args: errors.append(True))
    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    view._manage_photos()
    add_button = next(
        button for button in dialogs[0].findChildren(item_db_view.QPushButton)
        if button.text().startswith("\u2795"))
    add_button.click()

    assert db["items"][0]["images"] == ["existing.png"]
    assert not (tmp_path / "photos/partial.png").exists()
    assert not (tmp_path / "photos/fail.png").exists()
    assert errors == [True]
    view.close()



class _FakeFileDropEvent:
    def __init__(self, paths):
        self._mime = QMimeData()
        self._mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        self.accepted = False
        self.ignored = False

    def mimeData(self):
        return self._mime

    def source(self):
        return None

    def acceptProposedAction(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


def test_photo_manager_accepts_image_files_dropped_from_file_manager(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-drop.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    db = {"items": [{
        "id": "drop", "name": "Drop item", "images": [], "image": "",
    }]}
    saved = []
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db,
        save_db=lambda value: saved.append(list(value["items"][0]["images"])))
    view.table.selectRow(0)
    first = tmp_path / "first.png"
    second = tmp_path / "second.jpg"
    third = tmp_path / "third.webp"
    fourth = tmp_path / "fourth.bmp"
    ignored = tmp_path / "notes.txt"
    for path in (first, second, third, fourth, ignored):
        path.write_bytes(b"source")
    destinations = iter((
        "drop/first.png", "drop/second.png",
        "drop/third.png", "drop/fourth.png"))
    monkeypatch.setattr(item_db_view, "next_image_path",
                        lambda *_args: next(destinations))
    monkeypatch.setattr(item_db_view, "absolute_path",
                        lambda rel: str(tmp_path / rel))

    def copy_image(source, relative):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(source).read_bytes())

    monkeypatch.setattr(item_db_view, "copy_image_as_png", copy_image)
    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    view._manage_photos()
    photo_list = dialogs[0].findChild(item_db_view._PhotoDropListWidget)

    enter_event = _FakeFileDropEvent([first, ignored])
    photo_list.dragEnterEvent(enter_event)
    assert enter_event.accepted and not enter_event.ignored

    drop_event = _FakeFileDropEvent([first, second, third, fourth, ignored])
    photo_list.dropEvent(drop_event)
    expected = [
        "drop/first.png", "drop/second.png",
        "drop/third.png", "drop/fourth.png"]
    assert drop_event.accepted and not drop_event.ignored
    assert db["items"][0]["images"] == expected
    assert db["items"][0]["image"] == expected[0]
    assert saved == [expected]
    assert all((tmp_path / path).is_file() for path in expected)
    view.close()


@pytest.mark.parametrize("extension", [".webp", ".bmp"])
def test_copy_image_as_png_converts_webp_and_bmp(
        tmp_path, monkeypatch, extension):
    source = tmp_path / f"source{extension}"
    pixels = np.zeros((8, 8, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(extension, pixels)
    assert ok
    source.write_bytes(encoded.tobytes())
    target = tmp_path / "converted.png"
    monkeypatch.setattr(items_module, "absolute_path", lambda _rel: str(target))

    result = items_module.copy_image_as_png(str(source), "items/converted.png")

    assert result == str(target)
    assert target.is_file()
    converted = cv2.imread(str(target), cv2.IMREAD_COLOR)
    assert converted is not None
    assert converted.shape == pixels.shape


def test_image_file_dialog_filter_includes_webp_and_bmp(
        qapp, tmp_path, monkeypatch):
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: {"items": []},
        save_db=lambda _db: None)
    captured = {}

    def get_open_file_names(*args):
        captured["filter"] = args[3]
        return [], args[3]

    monkeypatch.setattr(
        item_db_view.QFileDialog, "getOpenFileNames", get_open_file_names)
    assert view._ask_image_files("Add photos") == []
    assert "*.webp" in captured["filter"]
    assert "*.bmp" in captured["filter"]
    view.close()


def test_photo_drop_list_rejects_non_image_files(qapp, tmp_path):
    text_file = tmp_path / "readme.txt"
    text_file.write_text("not an image", encoding="utf-8")
    photo_list = item_db_view._PhotoDropListWidget()
    event = _FakeFileDropEvent([text_file])
    photo_list.dragEnterEvent(event)
    assert event.ignored and not event.accepted


def test_edit_item_failed_save_restores_fields_and_renamed_photo(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-edit-rollback.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    old_photo = tmp_path / "diamond.png"
    old_photo.write_bytes(b"original diamond photo")
    original_item = {
        "id": "diamond-id", "name": "Diamond", "stack_size": 64,
        "category": "blocks", "enabled": True,
        "images": ["diamond.png"], "image": "diamond.png",
    }
    db = {"items": [dict(original_item)]}

    def fail_save(_db):
        raise OSError("simulated disk error")

    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=fail_save)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(
        item_db_view, "next_image_path", lambda *_args: "renamed/diamond_new.png")
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *_a, **_k: None))

    def accept_edited_dialog(dialog):
        dialog.findChild(item_db_view.QLineEdit, "itemNameEdit").setText("Diamond renamed")
        dialog.findChild(item_db_view.QComboBox, "stackPresetCombo").setCurrentIndex(1)
        dialog.findChild(item_db_view.QComboBox, "itemCategoryCombo").setCurrentIndex(1)
        dialog.accept()
        return int(item_db_view.QDialog.Accepted)

    monkeypatch.setattr(item_db_view.QDialog, "exec", accept_edited_dialog)
    view.table.selectRow(0)

    view._edit_item()

    assert db["items"] == [original_item]
    assert old_photo.read_bytes() == b"original diamond photo"
    assert not (tmp_path / "renamed/diamond_new.png").exists()
    assert view.table.item(0, 0).text() == "Diamond"
    view.close()


def test_edit_item_cleans_partial_photo_move_and_keeps_original(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-partial-move.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    old_photo = tmp_path / "diamond.png"
    old_photo.write_bytes(b"complete original image")
    target_photo = tmp_path / "renamed" / "diamond_new.png"
    original_item = {
        "id": "diamond-id", "name": "Diamond", "stack_size": 64,
        "category": "blocks", "enabled": True,
        "images": ["diamond.png"], "image": "diamond.png",
    }
    db = {"items": [dict(original_item)]}
    saved = []
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db,
        save_db=lambda value: saved.append(value))
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(
        item_db_view, "next_image_path", lambda *_args: "renamed/diamond_new.png")
    real_move = item_db_view.shutil.move

    def fail_after_partial_copy(source, destination):
        if str(source) == str(old_photo) and str(destination) == str(target_photo):
            target_photo.parent.mkdir(parents=True, exist_ok=True)
            target_photo.write_bytes(b"partial image")
            raise OSError("simulated cross-volume copy failure")
        return real_move(source, destination)

    monkeypatch.setattr(item_db_view.shutil, "move", fail_after_partial_copy)

    def accept_edited_dialog(dialog):
        dialog.findChild(item_db_view.QLineEdit, "itemNameEdit").setText("Diamond renamed")
        dialog.accept()
        return int(item_db_view.QDialog.Accepted)

    monkeypatch.setattr(item_db_view.QDialog, "exec", accept_edited_dialog)
    view.table.selectRow(0)

    view._edit_item()

    assert db["items"][0]["name"] == "Diamond renamed"
    assert db["items"][0]["images"] == ["diamond.png"]
    assert old_photo.read_bytes() == b"complete original image"
    assert not target_photo.exists()
    assert saved
    view.close()


def test_photo_manager_renames_photo_without_changing_its_contents(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-rename.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    source = tmp_path / "old.png"
    source.write_bytes(b"original photo bytes")
    destination = tmp_path / "renamed.png"
    db = {"items": [{
        "id": "photos", "name": "Test item",
        "images": ["old.png"], "image": "old.png",
    }]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(item_db_view, "next_image_path", lambda *_args: "renamed.png")
    monkeypatch.setattr(view, "_ask_text", lambda *_args: "renamed")
    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    view.table.selectRow(0)
    view._manage_photos()
    buttons = dialogs[0].findChildren(item_db_view.QPushButton)

    buttons[2].click()

    assert db["items"][0]["images"] == ["renamed.png"]
    assert db["items"][0]["image"] == "renamed.png"
    assert destination.read_bytes() == b"original photo bytes"
    assert not source.exists()
    view.close()


def test_photo_manager_rejects_existing_rename_destination(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-collision.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    source = tmp_path / "old.png"
    occupied = tmp_path / "occupied.png"
    source.write_bytes(b"original photo")
    occupied.write_bytes(b"do not overwrite")
    db = {"items": [{
        "id": "photos", "name": "Test item",
        "images": ["old.png"], "image": "old.png",
    }]}
    errors = []
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(item_db_view, "next_image_path", lambda *_args: "occupied.png")
    monkeypatch.setattr(view, "_ask_text", lambda *_args: "occupied")
    monkeypatch.setattr(
        item_db_view.QMessageBox, "critical",
        lambda *_args: errors.append(True))
    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    view.table.selectRow(0)
    view._manage_photos()

    dialogs[0].findChildren(item_db_view.QPushButton)[2].click()

    assert db["items"][0]["images"] == ["old.png"]
    assert source.read_bytes() == b"original photo"
    assert occupied.read_bytes() == b"do not overwrite"
    assert errors == [True]
    view.close()


def test_photo_manager_restores_original_file_when_database_save_fails(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-rename-save-failure.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    source = tmp_path / "old.png"
    source.write_bytes(b"original photo")
    destination = tmp_path / "renamed.png"
    db = {"items": [{
        "id": "photos", "name": "Test item",
        "images": ["old.png"], "image": "old.png",
    }]}
    errors = []
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db,
        save_db=lambda _db: (_ for _ in ()).throw(OSError("disk error")))
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(item_db_view, "next_image_path", lambda *_args: "renamed.png")
    monkeypatch.setattr(view, "_ask_text", lambda *_args: "renamed")
    monkeypatch.setattr(
        item_db_view.QMessageBox, "critical",
        lambda *_args: errors.append(True))
    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    view.table.selectRow(0)
    view._manage_photos()

    dialogs[0].findChildren(item_db_view.QPushButton)[2].click()

    assert db["items"][0]["images"] == ["old.png"]
    assert source.read_bytes() == b"original photo"
    assert not destination.exists()
    assert errors == [True]
    view.close()


def test_safe_photo_move_removes_partial_destination_on_failure(
        tmp_path, monkeypatch):
    source = tmp_path / "old.png"
    destination = tmp_path / "new.png"
    source.write_bytes(b"complete original")
    real_move = item_db_view.shutil.move

    def fail_after_partial_copy(src, dst):
        Path(dst).write_bytes(b"partial")
        raise OSError("simulated copy failure")

    monkeypatch.setattr(item_db_view.shutil, "move", fail_after_partial_copy)
    with pytest.raises(OSError, match="simulated copy failure"):
        item_db_view._move_photo_safely(str(source), str(destination))

    assert source.read_bytes() == b"complete original"
    assert not destination.exists()
    monkeypatch.setattr(item_db_view.shutil, "move", real_move)


def test_photo_manager_removes_file_after_successful_database_save_and_undo_restores_it(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-remove.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    photo = tmp_path / "remove.png"
    photo.write_bytes(b"photo to remove")
    db = {"items": [{
        "id": "photos", "name": "Test item",
        "images": ["remove.png"], "image": "remove.png",
    }]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(
        item_db_view.QMessageBox, "question",
        lambda *_args: item_db_view.QMessageBox.Yes)
    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    view.table.selectRow(0)
    view._manage_photos()

    dialogs[0].findChildren(item_db_view.QPushButton)[1].click()

    assert db["items"][0]["images"] == []
    assert db["items"][0]["image"] == ""
    assert not photo.exists()
    assert view.undo_button.isEnabled()

    view._undo_last_change()

    assert db["items"][0]["images"] == ["remove.png"]
    assert photo.read_bytes() == b"photo to remove"
    view.close()


def test_photo_manager_keeps_photo_and_refreshes_item_when_remove_save_fails(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-remove-failure.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    photo = tmp_path / "keep.png"
    photo.write_bytes(b"must remain")
    original = {
        "id": "photos", "name": "Test item",
        "images": ["keep.png"], "image": "keep.png",
    }
    db = {"items": [{**original, "images": ["keep.png"]}]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db,
        save_db=lambda _db: (_ for _ in ()).throw(OSError("disk error")))
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(
        item_db_view.QMessageBox, "question",
        lambda *_args: item_db_view.QMessageBox.Yes)
    monkeypatch.setattr(
        item_db_view.QMessageBox, "critical", staticmethod(lambda *_a, **_k: None))
    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    view.table.selectRow(0)
    view._manage_photos()

    dialogs[0].findChildren(item_db_view.QPushButton)[1].click()

    assert db["items"] == [original]
    assert photo.read_bytes() == b"must remain"
    assert dialogs[0].findChild(item_db_view.QListWidget).count() == 1
    assert dialogs[0].findChild(item_db_view.QListWidget).item(0).text().endswith("keep.png")
    view.close()


def test_photo_manager_make_primary_restores_order_when_save_fails(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-primary-failure.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    original = [str(first), str(second)]
    db = {"items": [{
        "id": "photos", "name": "Test item",
        "images": list(original), "image": str(first),
    }]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db,
        save_db=lambda _db: (_ for _ in ()).throw(OSError("disk error")))
    monkeypatch.setattr(
        item_db_view.QMessageBox, "critical", staticmethod(lambda *_a, **_k: None))
    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    view.table.selectRow(0)
    view._manage_photos()
    dialog = dialogs[0]
    photo_list = dialog.findChild(item_db_view.QListWidget)
    primary_button = dialog.findChildren(item_db_view.QPushButton)[3]
    photo_list.setCurrentRow(1)

    primary_button.click()

    assert db["items"][0]["images"] == original
    assert db["items"][0]["image"] == str(first)
    assert [photo_list.item(i).data(Qt.UserRole)
            for i in range(photo_list.count())] == original
    assert photo_list.currentRow() == 0
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    view.close()


def test_photo_manager_drag_reorder_restores_order_when_save_fails(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-drag-failure.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    original = ["first.png", "second.png", "third.png"]
    db = {"items": [{
        "id": "photos", "name": "Test item",
        "images": list(original), "image": original[0],
    }]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db,
        save_db=lambda _db: (_ for _ in ()).throw(OSError("disk error")))
    monkeypatch.setattr(
        item_db_view.QMessageBox, "critical", staticmethod(lambda *_a, **_k: None))
    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    view.table.selectRow(0)
    view._manage_photos()
    photo_list = dialogs[0].findChild(item_db_view.QListWidget)

    moved = photo_list.takeItem(2)
    photo_list.insertItem(0, moved)
    photo_list.setCurrentRow(0)
    photo_list.model().rowsMoved.emit(
        QModelIndex(), 2, 2, QModelIndex(), 0)
    qapp.processEvents()

    assert db["items"][0]["images"] == original
    assert db["items"][0]["image"] == original[0]
    assert [photo_list.item(i).data(Qt.UserRole)
            for i in range(photo_list.count())] == original
    assert photo_list.item(0).text().startswith("\u2605 ")
    view.close()


def test_delete_item_saves_before_removing_photos_and_undo_restores_them(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-delete-item.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    photo = tmp_path / "item.png"
    photo.write_bytes(b"item photo")
    db = {"items": [{
        "id": "remove-me", "name": "Remove me",
        "images": ["item.png"], "image": "item.png",
    }]}
    saved = []
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db,
        save_db=lambda value: saved.append(len(value["items"])))
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(
        item_db_view.QMessageBox, "question",
        lambda *_args: item_db_view.QMessageBox.Yes)
    view.table.selectRow(0)

    view._delete_item()

    assert db["items"] == []
    assert saved == [0]
    assert not photo.exists()
    assert view.undo_button.isEnabled()
    view._undo_last_change()
    assert db["items"][0]["images"] == ["item.png"]
    assert photo.read_bytes() == b"item photo"
    view.close()


def test_delete_item_save_failure_restores_database_photo_and_selection(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-delete-failure.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    photo = tmp_path / "keep.png"
    photo.write_bytes(b"keep photo")
    original = {
        "id": "keep", "name": "Keep item",
        "images": ["keep.png"], "image": "keep.png",
    }
    db = {"items": [dict(original)]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db,
        save_db=lambda _db: (_ for _ in ()).throw(OSError("disk error")))
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(
        item_db_view.QMessageBox, "question",
        lambda *_args: item_db_view.QMessageBox.Yes)
    monkeypatch.setattr(
        item_db_view.QMessageBox, "critical", staticmethod(lambda *_a, **_k: None))
    view.table.selectRow(0)

    view._delete_item()

    assert db["items"] == [original]
    assert photo.read_bytes() == b"keep photo"
    assert view._selected_item()["id"] == "keep"
    assert view.table.currentRow() == 0
    assert not view.undo_button.isEnabled()
    view.close()


def test_delete_selected_preserves_shared_photo_and_undo_restores_items(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-delete-multiple.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    shared = tmp_path / "shared.png"
    unique = tmp_path / "unique.png"
    shared.write_bytes(b"shared photo")
    unique.write_bytes(b"unique photo")
    original_items = [
        {"id": "one", "name": "One", "images": ["shared.png", "unique.png"]},
        {"id": "two", "name": "Two", "images": ["shared.png"]},
        {"id": "three", "name": "Three", "images": ["shared.png"]},
    ]
    db = {"items": [dict(item) for item in original_items]}
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=lambda _db: None)
    monkeypatch.setattr(item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(
        item_db_view.QMessageBox, "question",
        lambda *_args: item_db_view.QMessageBox.Yes)
    view.table.selectAll()
    # Select by stable IDs because the table is sorted alphabetically.
    view.table.clearSelection()
    for row in range(view.table.rowCount()):
        if view.table.item(row, 0).data(Qt.UserRole) in {"one", "two"}:
            view.table.selectionModel().select(
                view.table.model().index(row, 0),
                item_db_view.QItemSelectionModel.Select | item_db_view.QItemSelectionModel.Rows)

    view._delete_selected()

    assert [item["id"] for item in db["items"]] == ["three"]
    assert shared.read_bytes() == b"shared photo"
    assert not unique.exists()
    view._undo_last_change()
    assert [item["id"] for item in db["items"]] == ["one", "two", "three"]
    assert shared.read_bytes() == b"shared photo"
    assert unique.read_bytes() == b"unique photo"
    view.close()


def test_failed_partial_undo_file_restore_rolls_back_every_file(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-partial-undo.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    first = tmp_path / "first-removed.png"
    second = tmp_path / "second-removed.png"
    first.write_bytes(b"first original")
    second.write_bytes(b"second original")
    db = {"items": [{
        "id": "one", "name": "Item",
        "images": ["first-removed.png", "second-removed.png"],
    }]}
    save_calls = []
    def save_db(_db):
        save_calls.append(True)

    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=save_db)
    monkeypatch.setattr(
        item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))
    db["items"].clear()
    assert view._after_change(
        delete_files=("first-removed.png", "second-removed.png")) is True
    assert not first.exists() and not second.exists()

    real_apply = view._apply_undo_files
    apply_calls = []
    def fail_after_first_restore(file_state):
        apply_calls.append(True)
        if len(apply_calls) == 1:
            first_entry = {next(iter(file_state)): next(iter(file_state.values()))}
            real_apply(first_entry)
            raise OSError("simulated partial image restore failure")
        real_apply(file_state)
    monkeypatch.setattr(view, "_apply_undo_files", fail_after_first_restore)
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *_a, **_k: None))

    view._undo_last_change()

    assert db["items"] == []
    assert not first.exists()
    assert not second.exists()
    assert view.undo_button.isEnabled()
    assert len(apply_calls) == 2
    view.close()


def test_photo_order_and_primary_survive_close_reopen_and_database_reload(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-reopen.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    monkeypatch.setattr(
        item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))

    first_photo = tmp_path / "first.png"
    second_photo = tmp_path / "second.png"
    first_photo.write_bytes(b"first photo survives")
    second_photo.write_bytes(b"second photo survives")
    database_file = tmp_path / "items.json"
    original_db = {"items": [{
        "id": "photo-reopen", "name": "Diamond",
        "images": ["first.png", "second.png"], "image": "first.png",
    }]}
    save_json(str(database_file), original_db)
    db = load_json(str(database_file), {"items": []})

    def save_current(database):
        save_json(str(database_file), database)

    first_view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: db, save_db=save_current)
    first_view.table.selectRow(0)
    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    first_view._manage_photos()
    photo_dialog = dialogs[-1]
    photo_list = photo_dialog.findChild(item_db_view.QListWidget)
    photo_list.setCurrentRow(1)
    photo_dialog.findChildren(item_db_view.QPushButton)[3].click()

    assert db["items"][0]["images"] == ["second.png", "first.png"]
    assert db["items"][0]["image"] == "second.png"
    first_view.close()

    # Simulate a fresh application-side database load, not reuse of the old dict.
    reloaded_db = load_json(str(database_file), {"items": []})
    second_view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: reloaded_db,
        save_db=save_current)
    second_view.table.selectRow(0)

    assert reloaded_db["items"][0]["images"] == ["second.png", "first.png"]
    assert reloaded_db["items"][0]["image"] == "second.png"
    assert [second_view.photo_list.item(i).data(Qt.UserRole)
            for i in range(second_view.photo_list.count())] == [
                "second.png", "first.png"]
    second_view._manage_photos()
    reopened_photo_list = dialogs[-1].findChild(item_db_view.QListWidget)
    assert reopened_photo_list.item(0).text().startswith(chr(0x2605))
    assert reopened_photo_list.item(0).text().endswith("second.png")
    assert second_photo.read_bytes() == b"second photo survives"
    assert first_photo.read_bytes() == b"first photo survives"
    second_view.close()


def test_missing_and_corrupt_photos_are_identified_without_database_changes(
        qapp, tmp_path, monkeypatch):
    settings_file = tmp_path / "item-db-photo-errors.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    monkeypatch.setattr(
        item_db_view, "absolute_path", lambda rel: str(tmp_path / rel))

    valid = QPixmap(8, 8)
    assert valid.save(str(tmp_path / "valid.png"), "PNG")
    (tmp_path / "corrupt.png").write_bytes(b"not an image")
    database = {"items": [{
        "id": "photo-errors", "name": "Diamond",
        "images": ["missing.png", "corrupt.png", "valid.png"],
        "image": "missing.png",
    }]}
    original = {"items": [dict(database["items"][0])] }
    view = ItemDatabaseView(
        None, read_only=False, get_db=lambda: database,
        save_db=lambda _db: pytest.fail("Displaying photo errors must not save the database"))
    view.table.selectRow(0)

    missing_problem = item_db_view._photo_problem("missing.png")
    corrupt_problem = item_db_view._photo_problem("corrupt.png")
    assert missing_problem in view.photo_list.item(0).text()
    assert "missing.png" in view.photo_list.item(0).toolTip()
    assert corrupt_problem in view.photo_list.item(1).text()
    assert view.photo_list.item(2).text() == "valid.png"
    assert "missing.png" in view.table.item(0, 3).toolTip()
    assert "corrupt.png" not in view.table.item(0, 3).toolTip()

    view._show_photo_preview(0)
    assert missing_problem in view.preview.text()
    view._show_photo_preview(1)
    assert corrupt_problem in view.preview.text()
    assert database == original

    dialogs = []
    monkeypatch.setattr(
        item_db_view.QDialog, "exec",
        lambda dialog: dialogs.append(dialog) or 0)
    view._manage_photos()
    manager_list = dialogs[-1].findChild(item_db_view.QListWidget)
    assert missing_problem in manager_list.item(0).text()
    assert corrupt_problem in manager_list.item(1).text()
    assert manager_list.item(0).text().startswith(chr(0x2605) + " ")
    view.close()

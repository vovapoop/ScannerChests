"""GUI-тексты базы предметов и очереди неизвестных предметов."""
import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings as QtQSettings
from PySide6.QtWidgets import QApplication, QWidget

from chest_scanner import paths
from chest_scanner.gui import item_db_view
from chest_scanner.gui.item_db_view import ItemDatabaseView, UnknownItemsView


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_item_database_filter_and_detail_labels(qapp, tmp_path, monkeypatch):
    db = {
        "items": [{
            "id": "item-1", "name": "Алмаз", "stack_size": 64,
            "category": "uncategorized", "enabled": True, "images": [],
        }]
    }
    settings_file = tmp_path / "item-db.ini"
    monkeypatch.setattr(
        item_db_view, "QSettings",
        lambda *_args: QtQSettings(str(settings_file), QtQSettings.IniFormat))
    parent = QWidget()
    view = ItemDatabaseView(parent, read_only=False, get_db=lambda: db)
    view.table.selectRow(0)

    assert [view.status_combo.itemData(i) for i in range(view.status_combo.count())] == [
        "all", "enabled", "disabled"
    ]
    assert [view.status_combo.itemText(i) for i in range(view.status_combo.count())] == [
        "Все состояния (1)", "Вкл. (1)", "Выкл. (0)"
    ]
    assert view.cat_combo.toolTip() == "Фильтр по категории предмета"
    assert "Название: Алмаз" in view.details.text()
    assert "Стак: 64" in view.details.text()
    assert view.toggle_button.text() == "\u041e\u0442\u043a\u043b\u044e\u0447\u0438\u0442\u044c"
    assert view.toggle_button.toolTip() == "\u041e\u0442\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u0432\u0430\u043d\u0438\u0435"
    view.close()
    parent.close()


def test_unknown_queue_buttons_and_tooltip(qapp, tmp_path, monkeypatch):
    queue_file = tmp_path / "unknown.json"
    queue_file.write_text(json.dumps({"items": []}), encoding="utf-8")
    monkeypatch.setattr(paths, "UNKNOWN_QUEUE_FILE", str(queue_file))
    parent = QWidget()
    reader = SimpleNamespace(items_db={"items": []})
    view = UnknownItemsView(parent, reader, lambda _db: None)

    assert view.next_button.text() == "Следующий"
    assert view.dismiss_button.text() == "Удалить из очереди"
    assert "сначала идут самые частые" in view.queue_list.toolTip()
    view.close()
    parent.close()

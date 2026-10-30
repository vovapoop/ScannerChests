"""Тесты экспорта/импорта базы предметов (chest_scanner.item_pack).

Запуск:  .venv\\Scripts\\python.exe -m pytest tests\\test_item_pack.py -v
"""
import os
import zipfile

import pytest

from chest_scanner import item_pack


def _make_item(name, **extra):
    item = {"id": f"id-{name}", "name": name, "images": [], "image": "",
            "aliases": [], "enabled": True, "category": "uncategorized",
            "tags": []}
    item.update(extra)
    return item


def _make_db(*names, **extra_per_name):
    return {"version": 3, "items": [_make_item(n, **(extra_per_name.get(n) or {}))
                                    for n in names]}


def _zip_db(tmp_path, db, name="db.zip"):
    path = os.path.join(str(tmp_path), name)
    item_pack.export_item_db(db, path)
    return path


def test_export_creates_zip_with_items_json(tmp_path):
    zip_path = _zip_db(tmp_path, _make_db("Алмаз", "Изумруд"))
    assert os.path.isfile(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        assert "items.json" in zf.namelist()
    db, files = item_pack.read_item_db_zip(zip_path)
    assert {i["name"] for i in db["items"]} == {"Алмаз", "Изумруд"}
    assert files == {}


def test_export_skips_missing_icons(tmp_path):
    db = _make_db("Тест")
    db["items"][0]["images"] = ["items/нет_такого.png"]
    db["items"][0]["image"] = "items/нет_такого.png"
    zip_path = _zip_db(tmp_path, db)
    with zipfile.ZipFile(zip_path) as zf:
        assert "items/нет_такого.png" not in zf.namelist()


def test_import_merge_adds_new_and_updates_by_id(tmp_path):
    local = _make_db("Алмаз", "Локальный")
    incoming = _make_db("Изумруд", "Алмаз", Алмаз={"category": "rare"})
    zip_path = _zip_db(tmp_path, incoming)
    merged = item_pack.import_item_db(zip_path, local, mode="merge")
    names = {i["name"] for i in merged["items"]}
    assert names == {"Алмаз", "Локальный", "Изумруд"}
    diamond = next(i for i in merged["items"] if i["name"] == "Алмаз")
    assert diamond["id"] == "id-Алмаз"
    assert diamond["category"] == "rare"  # перезаписан версией из файла


def test_import_replace_replaces_whole_db(tmp_path):
    local = _make_db("Алмаз", "Локальный")
    incoming = _make_db("Изумруд")
    zip_path = _zip_db(tmp_path, incoming)
    merged = item_pack.import_item_db(zip_path, local, mode="replace")
    assert {i["name"] for i in merged["items"]} == {"Изумруд"}


def test_import_invalid_zip_raises(tmp_path):
    bad = os.path.join(str(tmp_path), "bad.zip")
    with open(bad, "wb") as f:
        f.write(b"not a zip archive at all")
    with pytest.raises(Exception):
        item_pack.import_item_db(bad, _make_db("А"), mode="replace")


def test_import_zip_without_items_json_raises(tmp_path):
    empty = os.path.join(str(tmp_path), "empty.zip")
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", "hello")
    with pytest.raises(ValueError):
        item_pack.import_item_db(empty, _make_db("А"), mode="replace")

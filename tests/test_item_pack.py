"""Тесты экспорта/импорта базы предметов (chest_scanner.item_pack).

Запуск:  .venv\\Scripts\\python.exe -m pytest tests\\test_item_pack.py -v
"""
import os
import zipfile

import pytest

from chest_scanner import item_pack


def _make_item(name, **extra):
    item = {"id": f"id-{name}", "name": name, "images": [], "image": "",
            "enabled": True, "category": "uncategorized"}
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


def test_export_refuses_missing_icons_without_replacing_existing_archive(tmp_path):
    db = _make_db("Тест")
    db["items"][0]["images"] = ["items/нет_такого.png"]
    db["items"][0]["image"] = "items/нет_такого.png"
    zip_path = os.path.join(str(tmp_path), "existing.zip")
    with open(zip_path, "wb") as stream:
        stream.write(b"previous archive")
    with pytest.raises(FileNotFoundError):
        item_pack.export_item_db(db, zip_path)
    with open(zip_path, "rb") as stream:
        assert stream.read() == b"previous archive"


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



def _write_archive(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)


def test_import_rejects_zip_slip_paths(tmp_path):
    path = tmp_path / "slip.zip"
    _write_archive(path, [
        ("items.json", '{"version":3,"items":[]}'),
        ("items/../../outside.png", b"not an image"),
    ])
    with pytest.raises(ValueError, match="недопустимый путь"):
        item_pack.read_item_db_zip(path)


def test_import_rejects_case_insensitive_duplicate_entries(tmp_path):
    path = tmp_path / "duplicate.zip"
    _write_archive(path, [
        ("items.json", '{"items":[]}'),
        ("items/A.png", b"a"),
        ("items/a.png", b"b"),
    ])
    with pytest.raises(ValueError, match="повторяющиеся"):
        item_pack.read_item_db_zip(path)


@pytest.mark.parametrize("payload", [
    "[]",
    '{"items":{}}',
    '{"items":[null]}',
    '{"items":[{"name":"X","images":["items/../../escape.png"]}]}',
])
def test_import_rejects_invalid_database_schema_and_paths(tmp_path, payload):
    path = tmp_path / "invalid-db.zip"
    _write_archive(path, [("items.json", payload)])
    with pytest.raises(ValueError):
        item_pack.read_item_db_zip(path)


def test_import_rejects_missing_referenced_image(tmp_path, monkeypatch):
    path = tmp_path / "missing-image.zip"
    db = _make_db("Тест", Тест={"images": ["items/missing.png"],
                                "image": "items/missing.png"})
    _write_archive(path, [("items.json", __import__("json").dumps(db, ensure_ascii=False))])
    monkeypatch.setattr(item_pack, "absolute_path", lambda _path: str(tmp_path / "missing.png"))
    with pytest.raises(ValueError, match="отсутствует изображение"):
        item_pack.read_item_db_zip(path)


def test_import_rejects_corrupt_image_bytes(tmp_path, monkeypatch):
    path = tmp_path / "corrupt-image.zip"
    db = _make_db("Тест", Тест={"images": ["items/icon.png"],
                                "image": "items/icon.png"})
    _write_archive(path, [
        ("items.json", __import__("json").dumps(db, ensure_ascii=False)),
        ("items/icon.png", b"not a png"),
    ])
    monkeypatch.setattr(item_pack, "absolute_path", lambda rel: str(tmp_path / rel))
    with pytest.raises(ValueError, match="Изображение"):
        item_pack.read_item_db_zip(path)


def test_import_rolls_back_new_icons_when_database_commit_fails(tmp_path, monkeypatch):
    import json

    import cv2
    import numpy as np

    path = tmp_path / "valid-image.zip"
    ok, encoded = cv2.imencode(".png", np.zeros((8, 8, 3), dtype=np.uint8))
    assert ok
    db = _make_db("Тест", Тест={"images": ["items/icon.png"],
                                "image": "items/icon.png"})
    _write_archive(path, [
        ("items.json", json.dumps(db, ensure_ascii=False)),
        ("items/icon.png", encoded.tobytes()),
    ])
    monkeypatch.setattr(item_pack, "absolute_path", lambda rel: str(tmp_path / rel))
    target = tmp_path / "items" / "icon.png"

    def fail_commit(_database):
        raise OSError("simulated database write failure")

    with pytest.raises(OSError, match="simulated"):
        item_pack.import_item_db(path, _make_db("Локальный"),
                                 mode="replace", commit=fail_commit)
    assert not target.exists()
    assert not list((tmp_path / "items").glob(".item-import-*"))


def test_import_commit_receives_database_after_icons_are_installed(tmp_path, monkeypatch):
    import json

    import cv2
    import numpy as np

    path = tmp_path / "valid-image-commit.zip"
    ok, encoded = cv2.imencode(".png", np.zeros((8, 8, 3), dtype=np.uint8))
    assert ok
    db = _make_db("Тест", Тест={"images": ["items/icon.png"],
                                "image": "items/icon.png"})
    _write_archive(path, [
        ("items.json", json.dumps(db, ensure_ascii=False)),
        ("items/icon.png", encoded.tobytes()),
    ])
    monkeypatch.setattr(item_pack, "absolute_path", lambda rel: str(tmp_path / rel))
    observed = {}

    def commit(database):
        observed["exists"] = (tmp_path / "items" / "icon.png").is_file()
        observed["count"] = len(database["items"])

    result = item_pack.import_item_db(path, _make_db("Локальный"),
                                      mode="replace", commit=commit)
    assert observed == {"exists": True, "count": 1}
    assert result["items"][0]["name"] == "Тест"


def test_import_rejects_unknown_mode(tmp_path):
    path = _zip_db(tmp_path, _make_db("Тест"))
    with pytest.raises(ValueError, match="режим"):
        item_pack.import_item_db(path, _make_db("Локальный"), mode="overwrite")



@pytest.mark.parametrize("icon_path", [
    "items/icon.png:alternate",
    "items/NUL.png",
    "items/folder./icon.png",
])
def test_import_rejects_windows_special_path_components(tmp_path, icon_path):
    import json

    path = tmp_path / "windows-path.zip"
    db = _make_db("Тест", Тест={"images": [icon_path], "image": icon_path})
    _write_archive(path, [("items.json", json.dumps(db, ensure_ascii=False))])
    with pytest.raises(ValueError):
        item_pack.read_item_db_zip(path)


def test_import_rejects_excessive_compression_ratio(tmp_path, monkeypatch):
    path = tmp_path / "ratio.zip"
    _write_archive(path, [
        ("items.json", '{"items":[]}'),
        ("items/unreferenced.bin", b"0" * 10000),
    ])
    monkeypatch.setattr(item_pack, "MAX_COMPRESSION_RATIO", 2)
    with pytest.raises(ValueError, match="степень сжатия"):
        item_pack.read_item_db_zip(path)


def test_import_rejects_too_many_archive_entries(tmp_path, monkeypatch):
    path = tmp_path / "many.zip"
    _write_archive(path, [
        ("items.json", '{"items":[]}'),
        ("items/a.bin", b"a"),
        ("items/b.bin", b"b"),
    ])
    monkeypatch.setattr(item_pack, "MAX_ARCHIVE_ENTRIES", 2)
    with pytest.raises(ValueError, match="слишком много"):
        item_pack.read_item_db_zip(path)


def test_import_rolls_back_prior_icons_if_later_install_fails(tmp_path, monkeypatch):
    import json

    import cv2
    import numpy as np

    path = tmp_path / "two-icons.zip"
    ok, encoded = cv2.imencode(".png", np.zeros((8, 8, 3), dtype=np.uint8))
    assert ok
    db = _make_db("Тест", Тест={
        "images": ["items/a.png", "items/b.png"], "image": "items/a.png"})
    _write_archive(path, [
        ("items.json", json.dumps(db, ensure_ascii=False)),
        ("items/a.png", encoded.tobytes()),
        ("items/b.png", encoded.tobytes()),
    ])
    monkeypatch.setattr(item_pack, "absolute_path", lambda rel: str(tmp_path / rel))
    real_link = item_pack.os.link
    calls = {"count": 0}

    def fail_second_link(source, target):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated install failure")
        return real_link(source, target)

    monkeypatch.setattr(item_pack.os, "link", fail_second_link)
    with pytest.raises(OSError, match="simulated"):
        item_pack.import_item_db(path, _make_db("Local"), mode="replace")
    assert not (tmp_path / "items" / "a.png").exists()
    assert not (tmp_path / "items" / "b.png").exists()


def test_export_and_import_round_trip_real_icon(tmp_path, monkeypatch):
    import cv2
    import numpy as np

    ok, encoded = cv2.imencode(".png", np.zeros((8, 8, 3), dtype=np.uint8))
    assert ok
    source_root = tmp_path / "source"
    source = source_root / "items" / "icon.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(encoded.tobytes())
    monkeypatch.setattr(item_pack, "absolute_path", lambda rel: str(source_root / rel))
    db = _make_db("Тест", Тест={"images": ["items/icon.png"],
                                "image": "items/icon.png"})
    archive = tmp_path / "portable.zip"
    item_pack.export_item_db(db, archive)
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {"items.json", "items/icon.png"}
    loaded, assets = item_pack.read_item_db_zip(archive)
    assert loaded["items"][0]["image"] == "items/icon.png"
    assert assets["items/icon.png"] == encoded.tobytes()


def test_import_rejects_profile_escape_through_symlinked_icon_directory(tmp_path, monkeypatch):
    import json

    import cv2
    import numpy as np

    outside = tmp_path / "outside"
    outside.mkdir()
    profile = tmp_path / "profile"
    profile.mkdir()
    try:
        (profile / "items").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    path = tmp_path / "symlink.zip"
    ok, encoded = cv2.imencode(".png", np.zeros((8, 8, 3), dtype=np.uint8))
    assert ok
    db = _make_db("Тест", Тест={"images": ["items/icon.png"],
                                "image": "items/icon.png"})
    _write_archive(path, [
        ("items.json", json.dumps(db, ensure_ascii=False)),
        ("items/icon.png", encoded.tobytes()),
    ])
    monkeypatch.setattr(item_pack, "absolute_path", lambda rel: str(profile / rel))
    with pytest.raises(ValueError, match="за пределы профиля"):
        item_pack.read_item_db_zip(path)



def test_import_rejects_crc_corrupted_zip_entry(tmp_path):
    path = tmp_path / "crc.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("items.json", '{"items":[]}')
        archive.writestr("items/extra.bin", b"checksum-protected-content")
    with zipfile.ZipFile(path, "r") as archive:
        info = archive.getinfo("items/extra.bin")
    raw = bytearray(path.read_bytes())
    data_offset = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
    raw[data_offset + 3] ^= 0x20
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="CRC"):
        item_pack.read_item_db_zip(path)



def test_import_normalizes_legacy_backslash_icon_paths(tmp_path, monkeypatch):
    import json

    import cv2
    import numpy as np

    path = tmp_path / "legacy-backslash.zip"
    ok, encoded = cv2.imencode(".png", np.zeros((8, 8, 3), dtype=np.uint8))
    assert ok
    db = _make_db("Тест", Тест={
        "images": ["items\\icon.png"], "image": "items\\icon.png"})
    _write_archive(path, [
        ("items.json", json.dumps(db, ensure_ascii=False)),
        ("items/icon.png", encoded.tobytes()),
    ])
    monkeypatch.setattr(item_pack, "absolute_path", lambda rel: str(tmp_path / rel))
    loaded, files = item_pack.read_item_db_zip(path)
    assert loaded["items"][0]["image"] == "items/icon.png"
    assert "items/icon.png" in files



def test_export_rejects_oversized_items_json_without_touching_destination(tmp_path, monkeypatch):
    path = tmp_path / "old.zip"
    path.write_bytes(b"old data")
    monkeypatch.setattr(item_pack, "MAX_FILE_BYTES", 32)
    with pytest.raises(ValueError, match="items.json"):
        item_pack.export_item_db(_make_db("A" * 100), path)
    assert path.read_bytes() == b"old data"


def test_imports_legacy_pre_manifest_item_database_archive(tmp_path):
    """Legacy item-database ZIP exports remain importable."""
    import json

    import cv2
    import numpy as np

    icon = np.zeros((2, 2, 4), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", icon)
    assert ok
    legacy_db = {
        "version": 2,
        "items": [{
            "name": "Старый предмет",
            "image": "items/legacy.png",
            "stack_size": 16,
            "category": "blocks",
        }],
    }
    archive_path = tmp_path / "legacy-items-export.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "items.json",
            json.dumps(legacy_db, ensure_ascii=False).encode("utf-8"),
        )
        archive.writestr("items/legacy.png", encoded.tobytes())

    imported, files = item_pack.read_item_db_zip(archive_path)

    assert imported["version"] == 3
    assert imported["items"][0]["name"] == "Старый предмет"
    assert imported["items"][0]["images"] == ["items/legacy.png"]
    assert imported["items"][0]["image"] == "items/legacy.png"
    assert imported["items"][0]["stack_size"] == 16
    assert set(files) == {"items/legacy.png"}

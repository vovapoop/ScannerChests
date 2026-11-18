"""Crash-safe JSON backup and recovery tests."""
import json

import pytest

from chest_scanner import commons


def _valid_items(value):
    return isinstance(value, dict) and isinstance(value.get("items"), list)


def test_corrupt_items_json_recovers_backup_and_preserves_original(tmp_path):
    path = tmp_path / "items.json"
    backup = tmp_path / "items.json.backup"
    expected = {"version": 3, "items": [{"id": "kept", "name": "Алмаз"}]}
    path.write_text('{"items": [', encoding="utf-8")
    backup.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")

    recovered = commons.load_json_recover(str(path), {"items": []}, _valid_items)

    assert recovered == expected
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    quarantined = list(tmp_path.glob("items.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == '{"items": ['
    assert json.loads(backup.read_text(encoding="utf-8")) == expected


def test_structurally_invalid_items_json_recovers_valid_backup(tmp_path):
    path = tmp_path / "items.json"
    expected = {"items": [{"id": "valid", "name": "Stone"}]}
    path.write_text(json.dumps({"items": None}), encoding="utf-8")
    (tmp_path / "items.json.backup").write_text(json.dumps(expected), encoding="utf-8")

    assert commons.load_json_recover(str(path), {"items": []}, _valid_items) == expected
    assert json.loads(path.read_text(encoding="utf-8")) == expected


def test_corrupt_items_without_backup_fails_closed_and_stays_preserved(tmp_path):
    path = tmp_path / "items.json"
    path.write_text('{broken', encoding="utf-8")

    with pytest.raises(ValueError, match="повреждён"):
        commons.load_json_recover(str(path), {"items": []}, _valid_items)

    assert not path.exists()
    assert [p.read_text(encoding="utf-8") for p in tmp_path.glob(
        "items.json.corrupt-*")] == ['{broken']
    with pytest.raises(ValueError, match="повреждённые копии"):
        commons.load_json_recover(str(path), {"items": []}, _valid_items)


def test_missing_items_file_recovers_backup_after_interrupted_restore(tmp_path):
    path = tmp_path / "items.json"
    expected = {"items": [{"id": "backup", "name": "Emerald"}]}
    (tmp_path / "items.json.backup").write_text(json.dumps(expected), encoding="utf-8")

    assert commons.load_json_recover(str(path), {"items": []}, _valid_items) == expected
    assert json.loads(path.read_text(encoding="utf-8")) == expected


def test_atomic_backup_survives_failure_of_main_file_replace(tmp_path, monkeypatch):
    path = tmp_path / "items.json"
    original = {"items": [{"id": "original"}]}
    path.write_text(json.dumps(original), encoding="utf-8")
    real_replace = commons.os.replace

    def fail_main_replace(source, destination):
        if str(destination) == str(path):
            raise PermissionError("simulated crash before main commit")
        return real_replace(source, destination)

    monkeypatch.setattr(commons.os, "replace", fail_main_replace)
    with pytest.raises(PermissionError, match="simulated"):
        commons.save_json(str(path), {"items": [{"id": "new"}]}, backup=True)

    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert json.loads((tmp_path / "items.json.backup").read_text(
        encoding="utf-8")) == original
    assert not list(tmp_path.glob("*.tmp"))


def test_save_json_backup_is_replaced_atomically(tmp_path):
    path = tmp_path / "queue.json"
    old = {"items": [{"id": "old"}]}
    new = {"items": [{"id": "new"}]}
    path.write_text(json.dumps(old), encoding="utf-8")
    commons.save_json(str(path), new, backup=True)

    assert json.loads(path.read_text(encoding="utf-8")) == new
    assert json.loads((tmp_path / "queue.json.backup").read_text(
        encoding="utf-8")) == old
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_process_json_writes_keep_main_and_backup_valid(tmp_path):
    import os
    import subprocess
    import sys

    path = tmp_path / "items.json"
    seed = {"items": [{"writer": "seed", "sequence": 0}]}
    path.write_text(json.dumps(seed), encoding="utf-8")
    script = "\n".join([
        "import json, sys",
        "from chest_scanner.commons import save_json",
        "path, writer = sys.argv[1:3]",
        "for sequence in range(8):",
        "    save_json(path, {'items': [{'writer': writer, 'sequence': sequence}]}, backup=True)",
    ])
    children = [subprocess.Popen(
        [sys.executable, "-c", script, str(path), f"writer-{index}"],
        cwd=os.getcwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for index in range(3)]

    for child in children:
        stdout, stderr = child.communicate(timeout=30)
        assert child.returncode == 0, (stdout, stderr)

    current = json.loads(path.read_text(encoding="utf-8"))
    backup = json.loads((tmp_path / "items.json.backup").read_text(encoding="utf-8"))
    for value in (current, backup):
        assert isinstance(value.get("items"), list)
        assert len(value["items"]) == 1
        item = value["items"][0]
        assert item["writer"] in {"seed", "writer-0", "writer-1", "writer-2"}
        assert 0 <= item["sequence"] <= 7
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob(".*.tmp"))


def test_process_crash_after_json_temp_flush_keeps_committed_file_and_allows_retry(
        tmp_path):
    import os
    import subprocess
    import sys

    path = tmp_path / "items.json"
    original = {"items": [{"id": "committed"}]}
    incoming = {"items": [{"id": "not-committed-yet"}]}
    path.write_text(json.dumps(original), encoding="utf-8")
    script = "\n".join([
        "import os, sys",
        "import chest_scanner.commons as commons",
        "path = sys.argv[1]",
        "def crash_after_temp_flush(source, destination):",
        "    if destination == path: os._exit(79)",
        "    return real_replace(source, destination)",
        "real_replace = commons.os.replace",
        "commons.os.replace = crash_after_temp_flush",
        "commons.save_json(path, {'items': [{'id': 'not-committed-yet'}]}, backup=True)",
    ])
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)], cwd=os.getcwd(),
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 79, (result.stdout, result.stderr)

    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert json.loads((tmp_path / "items.json.backup").read_text(
        encoding="utf-8")) == original
    abandoned = list(tmp_path.glob(".items.json.*.tmp"))
    assert len(abandoned) == 1
    assert json.loads(abandoned[0].read_text(encoding="utf-8")) == incoming

    # A normal subsequent save commits cleanly; the abandoned temp is never
    # mistaken for the live database or used to overwrite it during loading.
    retried = {"items": [{"id": "retry-committed"}]}
    commons.save_json(str(path), retried, backup=True)
    assert commons.load_json_recover(
        str(path), {"items": []}, _valid_items) == retried
    assert json.loads((tmp_path / "items.json.backup").read_text(
        encoding="utf-8")) == original
    assert abandoned[0].exists()

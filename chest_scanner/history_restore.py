"""Safe, additive restore of full JSON scan exports."""
import contextlib
import hashlib
import json
import os
import shutil
from datetime import datetime

from chest_scanner.commons import results_history_guard, save_json


def _read_json_bytes(path):
    try:
        with open(path, "rb") as stream:
            raw = stream.read()
    except FileNotFoundError:
        return None, None
    try:
        return raw, json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Некорректный JSON: {exc}") from exc


def _validate_record(record, index, source, require_id=True):
    if not isinstance(record, dict):
        raise ValueError(f"{source}: запись #{index} должна быть объектом.")
    record_id = record.get("id")
    if require_id and (not isinstance(record_id, (str, int)) or not str(record_id).strip()):
        raise ValueError(f"{source}: у записи #{index} отсутствует корректный ID.")
    for field, expected in (("slots", list), ("totals", dict), ("unknown_items", list)):
        if field in record and not isinstance(record[field], expected):
            raise ValueError(f"{source}: поле {field} в записи #{index} имеет неверный формат.")
    return str(record_id) if isinstance(record_id, (str, int)) and str(record_id).strip() else None


def prepare_history_restore(backup_path, results_path, existing_chests):
    """Validate an export and prepare an additive merge without writing anything."""
    backup_raw, backup_data = _read_json_bytes(backup_path)
    if backup_raw is None:
        raise ValueError("Файл резервной копии не найден.")
    if isinstance(backup_data, list):
        incoming = backup_data
    elif isinstance(backup_data, dict) and isinstance(backup_data.get("chests"), list):
        incoming = backup_data["chests"]
    else:
        raise ValueError("Неверный формат: ожидается массив сканирований или объект с chests.")

    current_raw, current_data = _read_json_bytes(results_path)
    if current_raw is None:
        current_data = {"chests": []}
        current_records = []
    else:
        if not isinstance(current_data, dict) or not isinstance(current_data.get("chests"), list):
            raise ValueError("Текущая история имеет неверный формат. Восстановление отменено.")
        current_records = current_data["chests"]

    known_ids = set()
    for index, record in enumerate(existing_chests, 1):
        record_id = _validate_record(record, index, "Текущая история", require_id=False)
        if record_id is not None:
            known_ids.add(record_id)
    for index, record in enumerate(current_records, 1):
        record_id = _validate_record(record, index, "Текущий файл", require_id=False)
        if record_id is not None:
            known_ids.add(record_id)

    existing_ids = set(known_ids)
    additions = []
    seen_backup_ids = set()
    skipped_existing = 0
    skipped_duplicates = 0
    for index, record in enumerate(incoming, 1):
        record_id = _validate_record(record, index, "Резервная копия")
        if record_id in seen_backup_ids:
            skipped_duplicates += 1
            continue
        seen_backup_ids.add(record_id)
        if record_id in known_ids:
            skipped_existing += 1
            continue
        additions.append(record)
        known_ids.add(record_id)

    merged = dict(current_data)
    merged["chests"] = list(current_records) + additions
    return {
        "backup_path": os.path.abspath(backup_path),
        "backup_hash": hashlib.sha256(backup_raw).hexdigest(),
        "results_path": os.path.abspath(results_path),
        "current_hash": hashlib.sha256(current_raw).hexdigest() if current_raw is not None else None,
        "existing_ids": tuple(sorted(existing_ids)),
        "merged": merged,
        "added_records": additions,
        "added": len(additions),
        "skipped_existing": skipped_existing,
        "skipped_duplicates": skipped_duplicates,
    }


def apply_history_restore(plan, existing_chests=None, lock_held=False):
    """Commit a prepared merge only if its source and history snapshot are unchanged."""
    guard = contextlib.nullcontext() if lock_held else results_history_guard(plan["results_path"])
    with guard:
        if existing_chests is not None:
            latest_ids = {
                str(record["id"]) for record in existing_chests
                if isinstance(record, dict) and record.get("id") is not None
                and str(record["id"]).strip()
            }
            if latest_ids != set(plan.get("existing_ids", ())):
                raise ValueError("История архивов изменилась после предварительного просмотра. Повторите операцию.")
        backup_raw, _ = _read_json_bytes(plan["backup_path"])
        if backup_raw is None or hashlib.sha256(backup_raw).hexdigest() != plan["backup_hash"]:
            raise ValueError("Файл резервной копии изменился после проверки. Повторите операцию.")
        current_raw, _ = _read_json_bytes(plan["results_path"])
        current_hash = hashlib.sha256(current_raw).hexdigest() if current_raw is not None else None
        if current_hash != plan["current_hash"]:
            raise ValueError("История изменилась после предварительного просмотра. Повторите операцию.")

        backup_copy = None
        if current_raw is not None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup_copy = f"{plan['results_path']}.before-restore-{stamp}.bak"
            shutil.copy2(plan["results_path"], backup_copy)
        save_json(plan["results_path"], plan["merged"])
        return backup_copy

"""SQLite-хранилище результатов сканирований (аналитика и резерв).

Основной путь данных остаётся JSON (results/chests.json + архивы в
results/history) — он используется существующими диалогами и экспортом.
SQLite-копия результатов ведётся параллельно из save_chest_result и
служит для быстрых статистических запросов, а также как резерв при
автоархивации истории.
"""

import json
import os
import sqlite3
import tempfile
import threading
from datetime import datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chests (
    id              TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    ts_sort         TEXT NOT NULL,
    occupied_slots  INTEGER DEFAULT 0,
    unique_items    INTEGER DEFAULT 0,
    unknown_count   INTEGER DEFAULT 0,
    screen_w        INTEGER DEFAULT 0,
    screen_h        INTEGER DEFAULT 0,
    data            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chests_ts_sort ON chests(ts_sort);
"""

_LOCAL = threading.local()


def _close_cached_connection(db_path=None):
    """Close this thread's cached connection before replacing a database file."""
    pair = getattr(_LOCAL, "pair", None)
    if pair is None or (db_path is not None and pair[0] != db_path):
        return
    try:
        pair[1].close()
    except sqlite3.Error:
        pass
    _LOCAL.pair = None


def _conn(db_path):
    """Return a thread-local SQLite connection, reopening it when needed."""
    pair = getattr(_LOCAL, "pair", None)
    if pair is not None and pair[0] != db_path:
        _close_cached_connection()
        pair = None
    if pair is None:
        conn = sqlite3.connect(db_path, timeout=10)
        pair = (db_path, conn)
        _LOCAL.pair = pair
    return pair[1]


def _is_database_corruption(error):
    message = str(error).lower()
    return any(marker in message for marker in (
        "not a database", "malformed", "database disk image is malformed",
        "file is encrypted or is not a database",
    ))


def _quarantine_database(db_path):
    """Move an unusable database aside without deleting its contents."""
    if not os.path.exists(db_path):
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = f"{db_path}.corrupt-{stamp}"
    _close_cached_connection(db_path)
    os.replace(db_path, backup_path)
    return backup_path


def _quarantine_if_corrupt(db_path):
    """Preserve an existing corrupt database before an atomic cache rebuild."""
    if not os.path.exists(db_path):
        return None
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        result = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as error:
        if not _is_database_corruption(error):
            raise
        if conn is not None:
            conn.close()
            conn = None
        return _quarantine_database(db_path)
    finally:
        if conn is not None:
            conn.close()
    if result and result[0] == "ok":
        return None
    return _quarantine_database(db_path)


def _ts_sort(value):
    """Преобразует "дд.мм.гггг чч:мм:сс" в сортируемый ISO-вид."""
    if not value or not isinstance(value, str):
        return ""
    try:
        from datetime import datetime
        return datetime.strptime(value, "%d.%m.%Y %H:%M:%S").isoformat()
    except (ValueError, TypeError):
        # Invalid legacy timestamps must never sort after a valid ISO timestamp.
        return ""


def init_db(db_path):
    """Create the schema, preserving and quarantining a corrupt database."""
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result and result[0] != "ok":
                raise sqlite3.DatabaseError(
                    f"database disk image is malformed: {result[0]}")
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'chests'"
            ).fetchone()
            if table_exists:
                columns = {row[1] for row in conn.execute(
                    "PRAGMA table_info(chests)").fetchall()}
                required = {
                    "id", "timestamp", "ts_sort", "occupied_slots",
                    "unique_items", "unknown_count", "screen_w", "screen_h",
                    "data",
                }
                if not required.issubset(columns):
                    raise sqlite3.DatabaseError(
                        "database disk image is malformed: incompatible chests schema")
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()
    except sqlite3.DatabaseError as error:
        if not _is_database_corruption(error):
            raise
        _quarantine_database(db_path)
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()


def add_chest(db_path, cd):
    """Добавляет результат сканирования в SQLite-копию. Идемпотентно по id."""
    if not isinstance(cd, dict) or not cd.get("id"):
        return False
    try:
        conn = _conn(db_path)
        conn.execute(
            "INSERT OR REPLACE INTO chests"
            "(id, timestamp, ts_sort, occupied_slots, unique_items, unknown_count,"
            " screen_w, screen_h, data)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (str(cd["id"]), str(cd.get("timestamp", "")),
             _ts_sort(cd.get("timestamp", "")),
             int(cd.get("occupied_slots", 0)),
             int(cd.get("unique_items", 0)),
             len(cd.get("unknown_items") or []),
             int((cd.get("screen_resolution") or [0, 0])[0] or 0),
             int((cd.get("screen_resolution") or [0, 0])[1] or 0),
             json.dumps(cd, ensure_ascii=False)))
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def load_all(db_path):
    """Все сканы в хронологическом порядке (из поля data)."""
    try:
        conn = _conn(db_path)
        rows = conn.execute("SELECT data FROM chests ORDER BY ts_sort ASC").fetchall()
    except sqlite3.Error:
        return []
    out = []
    for (raw,) in rows:
        try:
            out.append(json.loads(raw))
        except (ValueError, TypeError):
            continue
    return out


def count(db_path):
    """Количество сканирований в базе."""
    try:
        conn = _conn(db_path)
        row = conn.execute("SELECT COUNT(*) FROM chests").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def delete_last(db_path):
    """Удаляет последний скан (по времени) и возвращает его словарь.

    Возвращает None, если база пуста или недоступна. Тай-брейк по rowid
    для сканов с одинаковым timestamp.
    """
    try:
        conn = _conn(db_path)
        row = conn.execute(
            "SELECT id, data FROM chests ORDER BY ts_sort DESC, rowid DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    cid, raw = row[0], row[1]
    try:
        conn.execute("DELETE FROM chests WHERE id = ?", (cid,))
        conn.commit()
    except sqlite3.Error:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {"id": cid}


def stats(db_path, top_n=50):
    """Aggregate valid item counts while retaining presence-only observations."""
    chests = load_all(db_path)
    totals, freq = {}, {}
    for ch in chests:
        if not isinstance(ch, dict):
            continue
        chest_totals = ch.get("totals")
        if not isinstance(chest_totals, dict):
            chest_totals = {}
        slots = ch.get("slots")
        if not isinstance(slots, list):
            slots = []
        # Presence and quantity are separate facts: an item with an unreadable
        # stack count still contributes to unique-item and frequency metrics.
        names = {
            slot.get("item") for slot in slots
            if isinstance(slot, dict) and isinstance(slot.get("item"), str)
            and slot["item"]
        }
        names.update(name for name in chest_totals if isinstance(name, str) and name)
        for name in names:
            freq[name] = freq.get(name, 0) + 1
        for name, quantity in chest_totals.items():
            # Ignore malformed legacy quantities instead of truncating floats/bools.
            if (not isinstance(name, str) or not name or isinstance(quantity, bool)
                    or not isinstance(quantity, int) or quantity < 0):
                continue
            totals[name] = totals.get(name, 0) + quantity
    limit = max(0, int(top_n))
    top_qty = sorted(totals.items(), key=lambda item: (-item[1], item[0].casefold()))[:limit]
    top_freq = sorted(freq.items(), key=lambda item: (-item[1], item[0].casefold()))[:limit]
    return {
        "chests": len(chests),
        "items_total": sum(totals.values()),
        "unique_items": len(freq),
        "top_qty": top_qty,
        "top_freq": top_freq,
    }


def cleanup_stale_rebuild_files(directory, max_age_seconds=30 * 24 * 60 * 60, now=None):
    """Remove abandoned SQLite rebuild artifacts older than the safety window.

    Only files with our private rebuild prefix are eligible. Recent files are
    left alone because another process may still be building a replacement.
    Corrupt-database quarantines are deliberately retained as recovery copies.
    """
    import time

    if not os.path.isdir(directory):
        return 0
    try:
        age_limit = max(0, float(max_age_seconds))
    except (TypeError, ValueError, OverflowError):
        age_limit = 30 * 24 * 60 * 60
    current_time = time.time() if now is None else float(now)
    removed = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for name in names:
        if not name.startswith(".chests-rebuild-") or not name.endswith(".db"):
            continue
        path = os.path.join(directory, name)
        try:
            if not os.path.isfile(path) or current_time - os.path.getmtime(path) < age_limit:
                continue
            # A successful exclusive transaction confirms the candidate is not
            # currently in use by a SQLite writer before deleting it.
            conn = sqlite3.connect(path, timeout=0.1)
            try:
                conn.execute("BEGIN EXCLUSIVE")
                conn.rollback()
            finally:
                conn.close()
            for suffix in ("", "-wal", "-shm", "-journal"):
                candidate = path + suffix
                try:
                    os.remove(candidate)
                except FileNotFoundError:
                    pass
            removed += 1
        except (OSError, sqlite3.Error):
            # Locked, unreadable, or otherwise uncertain files are preserved.
            continue
    return removed


def sync_from_json(db_path, chests):
    """Atomically rebuild the SQLite cache from authoritative JSON history.

    The existing database remains untouched until the replacement is complete.
    Entries without IDs and duplicate IDs are ignored; the first occurrence wins.
    """
    if not isinstance(chests, list):
        raise ValueError("SQLite rebuild requires a complete JSON record list")
    directory = os.path.dirname(db_path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=".chests-rebuild-", suffix=".db", dir=directory)
    os.close(fd)
    _close_cached_connection(temp_path)
    added = 0
    seen_ids = set()
    try:
        init_db(temp_path)
        for chest in chests if isinstance(chests, list) else []:
            if not isinstance(chest, dict) or chest.get("id") is None:
                continue
            chest_id = str(chest["id"])
            if not chest_id or chest_id in seen_ids:
                continue
            seen_ids.add(chest_id)
            if not add_chest(temp_path, chest):
                raise sqlite3.DatabaseError(
                    f"Не удалось записать сканирование {chest_id} в временную SQLite-базу")
            added += 1
        _close_cached_connection(temp_path)
        quarantined_path = _quarantine_if_corrupt(db_path)
        _close_cached_connection(db_path)
        try:
            os.replace(temp_path, db_path)
        except OSError:
            # If publishing the rebuilt cache fails after quarantining a corrupt
            # original, put that original back at its canonical path when possible.
            if quarantined_path and os.path.exists(quarantined_path) and not os.path.exists(db_path):
                try:
                    os.replace(quarantined_path, db_path)
                except OSError:
                    # Keep the quarantine copy intact if rollback itself is blocked.
                    pass
            raise
        return added
    finally:
        _close_cached_connection(temp_path)
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def load_json_history(results_file, history_dir):
    """Load the authoritative current and archived JSON history strictly.

    The current file wins ID conflicts; archives are processed by filename.
    Malformed sources raise instead of silently rebuilding an incomplete cache.
    """
    records = []
    seen_ids = set()

    def append_entries(entries, source):
        if not isinstance(entries, list):
            raise ValueError(f"???????????? ?????? ????????????: {source}")
        for index, record in enumerate(entries):
            if not isinstance(record, dict):
                raise ValueError(f"???????????? ?????? ???????????? #{index + 1}: {source}")
            for field, expected in (("slots", list), ("totals", dict), ("unknown_items", list)):
                if field in record and not isinstance(record[field], expected):
                    raise ValueError(f"???????????? ???? {field} ? {source}, ?????? #{index + 1}")
            record_id = record.get("id")
            if record_id is not None:
                key = str(record_id)
                if not key.strip() or key in seen_ids:
                    continue
                seen_ids.add(key)
            records.append(record)

    if os.path.exists(results_file):
        with open(results_file, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict) or not isinstance(payload.get("chests"), list):
            raise ValueError(f"???????????? ?????? ???????: {results_file}")
        append_entries(payload["chests"], results_file)

    if os.path.isdir(history_dir):
        for filename in sorted(os.listdir(history_dir)):
            if not filename.endswith(".json"):
                continue
            source = os.path.join(history_dir, filename)
            with open(source, "r", encoding="utf-8") as stream:
                payload = json.load(stream)
            if not isinstance(payload, dict) or not isinstance(payload.get("chests"), list):
                raise ValueError(f"???????????? ????? ???????: {filename}")
            append_entries(payload["chests"], source)
    return records


def is_synced_with_json(db_path, chests):
    """Return whether SQLite exactly mirrors all ID-bearing JSON records."""
    expected = {}
    for chest in chests if isinstance(chests, list) else []:
        if not isinstance(chest, dict) or chest.get("id") is None:
            continue
        key = str(chest["id"])
        if key and key not in expected:
            expected[key] = chest
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            check = conn.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                return False
            rows = conn.execute("SELECT id, data FROM chests").fetchall()
        finally:
            conn.close()
        actual = {}
        for record_id, raw in rows:
            try:
                actual[str(record_id)] = json.loads(raw)
            except (TypeError, ValueError):
                return False
        return actual == expected
    except (OSError, sqlite3.Error):
        return False


def reconcile_from_json(db_path, results_file, history_dir):
    """Repair the SQLite cache from complete JSON history only when needed."""
    cleanup_stale_rebuild_files(os.path.dirname(db_path) or ".")
    chests = load_json_history(results_file, history_dir)
    if is_synced_with_json(db_path, chests):
        return False
    sync_from_json(db_path, chests)
    return True

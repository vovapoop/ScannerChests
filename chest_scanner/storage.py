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
import threading

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


def _conn(db_path):
    """Соединение SQLite, кэшированное по пути в текущем потоке."""
    pair = getattr(_LOCAL, "pair", None)
    if pair is None or pair[0] != db_path:
        conn = sqlite3.connect(db_path, timeout=10)
        pair = (db_path, conn)
        _LOCAL.pair = pair
    return pair[1]


def _ts_sort(value):
    """Преобразует "дд.мм.гггг чч:мм:сс" в сортируемый ISO-вид."""
    if not value or not isinstance(value, str):
        return ""
    try:
        from datetime import datetime
        return datetime.strptime(value, "%d.%m.%Y %H:%M:%S").isoformat()
    except (ValueError, TypeError):
        return str(value)


def init_db(db_path):
    """Создаёт таблицы, если их нет."""
    d = os.path.dirname(db_path)
    if d:
        os.makedirs(d, exist_ok=True)
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


def stats(db_path, top_n=50):
    """Агрегированная статистика: количество, топ по количеству и частоте."""
    chests = load_all(db_path)
    totals, freq = {}, {}
    for ch in chests:
        for n, q in (ch.get("totals") or {}).items():
            v = int(q or 0)
            totals[n] = totals.get(n, 0) + v
            freq[n] = freq.get(n, 0) + 1
    top_qty = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:top_n]
    top_freq = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return {
        "chests": len(chests),
        "items_total": sum(totals.values()),
        "unique_items": len(totals),
        "top_qty": top_qty,
        "top_freq": top_freq,
    }


def sync_from_json(db_path, chests):
    """Идемпотентно заполняет базу из списка сканов (например миграция)."""
    init_db(db_path)
    added = 0
    for cd in chests if isinstance(chests, list) else []:
        if add_chest(db_path, cd):
            added += 1
    return added
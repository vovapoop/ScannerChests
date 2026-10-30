"""Тесты экспорта (CSV/JSON) и разбиения сообщений Discord."""
import os

from chest_scanner.discord import split_discord_message
from chest_scanner.export import (
    export_chests_to_csv,
    export_chests_to_json,
    export_summary_to_csv,
)


def _chests():
    return [{
        "id": "abc123def", "timestamp": "01.01.2026 12:00:00",
        "slots": [{"item": "Алмаз", "category": "rare",
                   "quantity": 6, "unknown": False}],
        "totals": {"Алмаз": 6}, "unknown_items": [],
    }]


def test_split_plain_long_lines():
    parts = split_discord_message("a" * 4000, limit=1000)
    assert len(parts) > 1
    assert all(len(p) <= 1000 for p in parts)


def test_split_code_block_keeps_fences():
    text = "```text\n" + "\n".join(f"| {i} |" for i in range(200)) + "\n```"
    parts = split_discord_message(text, limit=1800)
    assert parts
    assert all("```" in p for p in parts)


def test_split_empty():
    assert split_discord_message("") == []
    assert split_discord_message(None) == []


def test_export_csv(tmp_path):
    path = os.path.join(str(tmp_path), "out.csv")
    export_chests_to_csv(_chests(), path)
    assert os.path.isfile(path)
    with open(path, encoding="utf-8-sig") as f:
        assert "Алмаз" in f.read()


def test_export_summary(tmp_path):
    path = os.path.join(str(tmp_path), "s.csv")
    export_summary_to_csv(_chests(), path)
    assert os.path.isfile(path)


def test_export_json(tmp_path):
    path = os.path.join(str(tmp_path), "out.json")
    export_chests_to_json(_chests(), path)
    assert os.path.isfile(path)
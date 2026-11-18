"""Тесты экспорта (CSV/JSON) и разбиения сообщений Discord."""
import os

from chest_scanner.discord import split_discord_message
from chest_scanner.export import (
    export_chests_to_csv,
    export_chests_to_json,
    export_summary_to_csv,
)
from chest_scanner.scanner.discord import DiscordMixin


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

def test_export_csv_marks_unknown_quantity_as_blank(tmp_path):
    path = os.path.join(str(tmp_path), "unknown.csv")
    chest = {
        "id": "unknown1",
        "timestamp": "01.01.2026 12:00:00",
        "slots": [{"item": "Железо", "category": "ore", "quantity": None}],
        "totals": {},
        "unknown_items": [],
    }
    export_chests_to_csv([chest], path)
    with open(path, encoding="utf-8-sig") as f:
        rows = f.read().splitlines()
    assert any("Железо;ore;;Нет" in row for row in rows)


def test_export_summary_ignores_unknown_quantities(tmp_path):
    path = os.path.join(str(tmp_path), "summary_unknown.csv")
    export_summary_to_csv([
        {"slots": [{"item": "Железо", "category": "ore", "quantity": None}],
         "totals": {}}
    ], path)
    with open(path, encoding="utf-8-sig") as f:
        content = f.read()
    assert "Железо" not in content


def test_export_summary_uses_custom_stack_sizes(tmp_path):
    path = os.path.join(str(tmp_path), "summary_custom_stack.csv")
    export_summary_to_csv(
        [{"totals": {"CustomStack": 32}}], path,
        stack_sizes={"CustomStack": 16},
    )
    with open(path, encoding="utf-8-sig") as f:
        rows = f.read().splitlines()
    assert any("CustomStack;uncategorized;32;2" in row for row in rows)


class _DiscordReport(DiscordMixin):
    config = {"discord_message": {}}

    @staticmethod
    def get_item_stack_size(_name, default=64):
        return 16


def test_discord_report_counts_unknown_quantities_from_legacy_slots():
    chest = {
        "timestamp": "01.01.2026 12:00:00",
        "slots": [
            {"item": "Known", "quantity": 16},
            {"item": "UnknownCount", "quantity": None},
            {"item": "", "quantity": None},
        ],
        "totals": {"Known": 16},
        "unknown_items": [],
    }
    message = _DiscordReport().build_discord_message([chest])
    assert "\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u043d\u0435 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d\u043e \u0432 \u044f\u0447\u0435\u0439\u043a\u0430\u0445: 1" in message
    assert "| Known" in message
    assert "|          1 |" in message


def test_discord_report_prefers_saved_unknown_quantity_count():
    chest = {
        "timestamp": "01.01.2026 12:00:00",
        "slots": [{"item": "UnknownCount", "quantity": None}],
        "quantity_unknown_slots": 3,
        "totals": {},
        "unknown_items": [],
    }
    message = _DiscordReport().build_discord_message([chest])
    assert "\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u043d\u0435 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d\u043e \u0432 \u044f\u0447\u0435\u0439\u043a\u0430\u0445: 3" in message


def test_export_json_preserves_unknown_quantity_as_null(tmp_path):
    path = os.path.join(str(tmp_path), "unknown.json")
    chest = {"slots": [{"item": "UnknownCount", "quantity": None}], "totals": {}}
    export_chests_to_json([chest], path)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert '"quantity": null' in content


def test_export_csv_writes_none_total_as_blank_and_accepts_missing_collections(tmp_path):
    path = os.path.join(str(tmp_path), "none_total.csv")
    export_chests_to_csv([{"totals": {"UnknownCount": None}, "slots": None}], path)
    with open(path, encoding="utf-8-sig") as f:
        rows = f.read().splitlines()
    assert any(row.split(";")[:5] == ["?", "?", "UnknownCount", "uncategorized", ""] and row.endswith("\u041d\u0435\u0442") for row in rows)


def test_export_summary_accepts_none_slots_and_totals(tmp_path):
    path = os.path.join(str(tmp_path), "empty_summary.csv")
    export_summary_to_csv([{"slots": None, "totals": None}], path)
    with open(path, encoding="utf-8-sig") as f:
        rows = f.read().splitlines()
    assert len(rows) == 1



def test_export_csv_preserves_full_scan_identifier(tmp_path):
    path = os.path.join(str(tmp_path), "full_id.csv")
    chest = {"id": "12345678-1234-5678-1234-567812345678",
             "totals": {"Stone": 1}, "slots": []}
    export_chests_to_csv([chest], path)
    with open(path, encoding="utf-8-sig", newline="") as stream:
        rows = list(__import__("csv").reader(stream, delimiter=";"))
    assert rows[1][0] == chest["id"]


def test_export_csv_escapes_spreadsheet_formula_cells(tmp_path):
    path = os.path.join(str(tmp_path), "formula.csv")
    chest = {
        "id": "=HYPERLINK(\"https://example.invalid\")",
        "timestamp": "  +1+1",
        "slots": [{"item": "@SUM(1,1)", "category": "=1+1", "quantity": 1}],
        "totals": {"@SUM(1,1)": 1},
    }
    export_chests_to_csv([chest], path)
    with open(path, encoding="utf-8-sig", newline="") as stream:
        rows = list(__import__("csv").reader(stream, delimiter=";"))
    assert rows[1][0].startswith("'")
    assert rows[1][1].startswith("'")
    assert rows[1][2].startswith("'")
    assert rows[1][3].startswith("'")


def test_export_summary_uses_newest_category_for_item(tmp_path):
    path = os.path.join(str(tmp_path), "category.csv")
    # History is supplied newest first by load_all_chests.
    export_summary_to_csv([
        {"slots": [{"item": "Stone", "category": "new-category"}],
         "totals": {"Stone": 2}},
        {"slots": [{"item": "Stone", "category": "old-category"}],
         "totals": {"Stone": 3}},
    ], path)
    with open(path, encoding="utf-8-sig", newline="") as stream:
        rows = list(__import__("csv").reader(stream, delimiter=";"))
    assert rows[1][1] == "new-category"
    assert rows[1][2] == "5"

"""??????? ??????????? ???????????? ? CSV/JSON."""
import csv
import json


def _csv_safe_cell(value):
    """Prevent spreadsheet applications from evaluating exported text as a formula."""
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value


def export_chests_to_csv(chests, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(_csv_safe_cell(value) for value in [
            "ID", "\u0414\u0430\u0442\u0430", "\u041f\u0440\u0435\u0434\u043c\u0435\u0442",
            "\u041a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f", "\u041a\u043e\u043b-\u0432\u043e",
            "\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0439",
        ])
        for chest in chests:
            # Keep the complete identifier: exports are also used to trace and reconcile scans.
            cid = str(chest.get("id", "?"))
            timestamp = chest.get("timestamp", "?")
            slots = chest.get("slots") or []
            totals = chest.get("totals") or {}
            unknown_items = set(chest.get("unknown_items") or [])
            item_categories = {}
            for slot in slots:
                if isinstance(slot, dict) and slot.get("item"):
                    item_categories[slot["item"]] = slot.get("category", "uncategorized")
            for name, quantity in sorted(totals.items()):
                writer.writerow(_csv_safe_cell(value) for value in [
                    cid, timestamp, name, item_categories.get(name, "uncategorized"),
                    "" if quantity is None else quantity,
                    "\u0414\u0430" if name in unknown_items else "\u041d\u0435\u0442",
                ])
            for slot in slots:
                if (isinstance(slot, dict) and slot.get("quantity") is None
                        and slot.get("item")):
                    writer.writerow(_csv_safe_cell(value) for value in [
                        cid, timestamp, slot["item"],
                        slot.get("category", "uncategorized"), "",
                        "\u0414\u0430" if slot["item"] in unknown_items else "\u041d\u0435\u0442",
                    ])
    return path


def export_summary_to_csv(chests, path, stack_sizes=None):
    totals, categories = {}, {}
    for chest in chests:
        item_categories = {}
        for slot in (chest.get("slots") or []):
            if isinstance(slot, dict) and slot.get("item"):
                item_categories[slot["item"]] = slot.get("category", "uncategorized")
        for name, quantity in (chest.get("totals") or {}).items():
            if quantity is None:
                continue
            totals[name] = totals.get(name, 0) + quantity
            # load_all_chests supplies newest-first history; retain the newest category.
            categories.setdefault(name, item_categories.get(name, "uncategorized"))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(_csv_safe_cell(value) for value in [
            "\u041f\u0440\u0435\u0434\u043c\u0435\u0442", "\u041a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f",
            "\u0412\u0441\u0435\u0433\u043e", "\u0421\u0442\u0430\u043a\u043e\u0432",
        ])
        for name, quantity in sorted(totals.items(), key=lambda pair: pair[0].lower()):
            try:
                stack_size = max(1, int((stack_sizes or {}).get(name, 64)))
            except (TypeError, ValueError):
                stack_size = 64
            writer.writerow(_csv_safe_cell(value) for value in [
                name, categories.get(name, "uncategorized"), quantity,
                quantity // stack_size,
            ])
    return path


def export_chests_to_json(chests, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(chests, f, ensure_ascii=False, indent=2)
    return path

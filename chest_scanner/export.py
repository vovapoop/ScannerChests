"""Экспорт результатов сканирования в CSV/JSON."""
import csv
import json


def export_chests_to_csv(chests, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["ID", "Время", "Предмет", "Категория", "Кол-во", "Неизвестный"])
        for ch in chests:
            cid = str(ch.get("id", "?"))[:8]
            ts = ch.get("timestamp", "?")
            ic = {}
            for s in ch.get("slots", []):
                ic[s.get("item", "")] = s.get("category", "uncategorized")
            for name, qty in sorted(ch.get("totals", {}).items()):
                w.writerow([cid, ts, name, ic.get(name, "uncategorized"), qty,
                            "Да" if name in ch.get("unknown_items", []) else "Нет"])
    return path


def export_summary_to_csv(chests, path):
    totals, cats = {}, {}
    for ch in chests:
        ic = {}
        for s in ch.get("slots", []):
            ic[s.get("item", "")] = s.get("category", "uncategorized")
        for name, qty in ch.get("totals", {}).items():
            totals[name] = totals.get(name, 0) + qty
            cats[name] = ic.get(name, "uncategorized")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Предмет", "Категория", "Всего", "Стаков"])
        for name, qty in sorted(totals.items(), key=lambda x: x[0].lower()):
            w.writerow([name, cats.get(name, "uncategorized"), qty, qty // 64])
    return path


def export_chests_to_json(chests, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(chests, f, ensure_ascii=False, indent=2)
    return path

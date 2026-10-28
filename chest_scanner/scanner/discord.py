"""ChestReader mixin: отправка сообщений в Discord."""
import os
import time
import unicodedata

import requests

from chest_scanner.commons import load_json, save_json
from chest_scanner.discord import split_discord_message
from chest_scanner.logging_setup import logger, now_moscow
from chest_scanner import paths

class DiscordMixin:
    """Methods extracted from the original ChestReader."""

    @staticmethod
    def format_number(n):
        return f"{n:,}".replace(",", " ")

    def build_discord_message(self, chests):
        if not chests:
            return "**База сундуков пуста**\nСначала откройте сундук и нажмите F8."
        dc = self.config.get("discord_message", {})
        gt, au = {}, set()
        for ch in chests:
            for n, q in ch.get("totals", {}).items():
                gt[n] = gt.get(n, 0) + q
            for u in ch.get("unknown_items", []):
                au.add(u)
        lt = chests[-1].get("timestamp", "?")
        try:
            dss = max(1, int(dc.get("stack_size", 64)))
        except (TypeError, ValueError):
            dss = 64
        tc = dc.get("table", {})
        nw = max(12, int(tc.get("name_width", 30)))
        tw = max(8, int(tc.get("total_width", 12)))
        sw = max(6, int(tc.get("stacks_width", 10)))

        def dw(v):
            w = 0
            for c in str(v):
                if unicodedata.combining(c):
                    continue
                w += 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
            return w

        def trunc(v, width):
            t = str(v)
            if dw(t) <= width:
                return t
            r, cw = [], 0
            tgt = max(1, width - 1)
            for c in t:
                chw = dw(c)
                if cw + chw > tgt:
                    break
                r.append(c)
                cw += chw
            return "".join(r) + "…"

        def fit(t, width, align="left"):
            t = trunc(t, width)
            p = max(0, width - dw(t))
            return " " * p + t if align == "right" else t + " " * p

        def tl(n, tot, st):
            return f"| {fit(n, nw)} | {fit(tot, tw, 'right')} | {fit(st, sw, 'right')} |"

        sep = f"+-{'-' * nw}-+-{'-' * tw}-+-{'-' * sw}-+"
        cols = tl("Название", "Всего", "Стаков")
        lines = []
        for nm, qty in sorted(gt.items(), key=lambda x: x[0].lower()):
            iss = self.get_item_stack_size(nm, dss)
            try:
                iss = max(1, int(iss))
            except (TypeError, ValueError):
                iss = dss
            st = "" if qty < iss else str(int(qty) // iss)
            lines.append(tl(nm, self.format_number(qty), st))
        it = "```text\n" + "\n".join([cols, sep] + lines) + "\n```"
        vals = {"chests": len(chests), "last_scan": lt, "items": it,
                "unknowns": "\n".join(sorted(au))}
        hdr = dc.get("header", "")
        s_ch = dc.get("scanned_chests", "Отсканировано сундуков: {chests}").format(**vals)
        l_sc = dc.get("last_scan", "Время последнего сканирования: {last_scan}").format(**vals)
        i_ti = dc.get("items_title", "══════════Предметы══════════")
        out = []
        if hdr:
            out.extend([hdr, ""])
        out.extend([s_ch, l_sc, "", i_ti, "", it])
        if au:
            ut = dc.get("unknown_title", "══════════Неизвестные предметы══════════")
            utpl = dc.get("unknown_item", "• {name}")
            utx = dc.get("item_separator", "\n").join(utpl.format(name=n) for n in sorted(au))
            out.extend(["", ut, "", utx])
        return "\n".join(out)

    def get_discord_webhook_url(self):
        env = self.config.get("discord_webhook_env", "DISCORD_WEBHOOK_URL")
        if os.path.exists(paths.DISCORD_WEBHOOK_FILE):
            try:
                d = load_json(paths.DISCORD_WEBHOOK_FILE, {})
                if isinstance(d, dict):
                    u = d.get("webhook_url") or d.get(env) or d.get("DISCORD_WEBHOOK_URL")
                    if isinstance(u, str) and u.strip():
                        return u.strip()
            except Exception:
                pass
        u = os.getenv(env, "").strip()
        return u or None

    @staticmethod
    def load_discord_ids():
        d = load_json(paths.DISCORD_IDS_FILE, {"message_ids": []})
        return [str(m) for m in (d.get("message_ids", []) if isinstance(d, dict) else []) if m]

    @staticmethod
    def save_discord_ids(ids):
        save_json(paths.DISCORD_IDS_FILE, {"message_ids": [str(i) for i in ids]})

    @staticmethod
    def _discord_err(r):
        try:
            b = r.json()
            if isinstance(b, dict) and b.get("message"):
                return str(b["message"])
        except ValueError:
            pass
        return r.text[:300]

    def _create_msg(self, url, content):
        try:
            r = requests.post(url, params={"wait": "true"},
                              json={"content": content, "username": "Chest Scanner"}, timeout=30)
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}: {self._discord_err(r)}"
            try:
                mid = r.json().get("id")
            except ValueError:
                mid = None
            return (str(mid), None) if mid else (None, "Нет ID")
        except requests.RequestException as e:
            return None, f"Сеть: {e}"

    def _edit_msg(self, url, mid, content):
        try:
            r = requests.patch(f"{url}/messages/{mid}",
                               json={"content": content, "username": "Chest Scanner"}, timeout=30)
            if r.status_code == 404:
                return False, "not_found"
            if r.status_code != 200:
                return False, f"HTTP {r.status_code}: {self._discord_err(r)}"
            return True, None
        except requests.RequestException as e:
            return False, f"Сеть: {e}"

    def _delete_msg(self, url, mid):
        try:
            r = requests.delete(f"{url}/messages/{mid}", timeout=15)
            if r.status_code in (200, 204, 404):
                return True, None
            return False, f"HTTP {r.status_code}"
        except requests.RequestException as e:
            return False, f"Сеть: {e}"

    def archive_report(self):
        data = load_json(paths.RESULTS_FILE, {"chests": []})
        chests = data.get("chests", []) if isinstance(data, dict) else []
        if not chests:
            return None
        ts = now_moscow().strftime("%Y-%m-%d_%H-%M-%S")
        hp = os.path.join(paths.HISTORY_DIR, f"report_{ts}.json")
        c = 2
        while os.path.exists(hp):
            hp = os.path.join(paths.HISTORY_DIR, f"report_{ts}_{c}.json")
            c += 1
        save_json(hp, data)
        save_json(paths.RESULTS_FILE, {"chests": []})
        return hp

    def send_all_chests_to_discord(self):
        url = self.get_discord_webhook_url()
        if not url:
            return False, "Discord Webhook не найден."
        if not url.startswith(("https://discord.com/api/webhooks/",
                               "https://discordapp.com/api/webhooks/")):
            return False, "Некорректный URL."
        if not os.path.exists(paths.RESULTS_FILE):
            return False, "chests.json не найден."
        data = load_json(paths.RESULTS_FILE, {"chests": []})
        chests = data.get("chests", []) if isinstance(data, dict) else []
        if not chests:
            return False, "Нет сканирований."
        msg = self.build_discord_message(chests)
        parts = split_discord_message(msg, limit=1800) or ["Пусто."]
        old = self.load_discord_ids()
        new, ed, cr, de = [], 0, 0, 0
        try:
            for i, content in enumerate(parts):
                try:
                    if len(content) > 1900:
                        raise RuntimeError("Превышен лимит Discord.")
                    if i < len(old):
                        ok, err = self._edit_msg(url, old[i], content)
                        if ok:
                            new.append(old[i]); ed += 1
                        elif err == "not_found":
                            cid, ce = self._create_msg(url, content)
                            if not cid:
                                raise RuntimeError(f"Восстановление: {ce}")
                            new.append(cid); cr += 1
                        else:
                            raise RuntimeError(f"Редактирование {old[i]}: {err}")
                    else:
                        cid, ce = self._create_msg(url, content)
                        if not cid:
                            raise RuntimeError(f"Создание: {ce}")
                        new.append(cid); cr += 1
                except Exception:
                    # Чекпоинт: сохранить уже полученные ID, чтобы при повторе
                    # не создавать дубликаты.
                    self.save_discord_ids(new)
                    raise
                time.sleep(0.5)
            if self.config.get("discord_delete_old_messages", True):
                del_errors = []
                for mid in old[len(parts):]:
                    ok, err = self._delete_msg(url, mid)
                    if ok:
                        de += 1
                    else:
                        del_errors.append(err or f"Удаление {mid}")
                        logger.warning(f"Discord: не удалось удалить {mid}: {err}")
                    time.sleep(0.3)
                self.save_discord_ids(new)
                if del_errors:
                    return True, (f"Изменено {ed}, создано {cr}, удалено {de}. "
                                  f"Внимание: {del_errors}")
            hp = self.archive_report()
            summary = f"Изменено {ed}, создано {cr}, удалено {de}."
            if hp:
                return True, summary + f" Архив: {os.path.basename(hp)}."
            return True, summary
        except requests.RequestException as e:
            return False, f"Сеть: {e}"
        except Exception as e:
            logger.error(f"Discord ошибка: {e}", exc_info=True)
            return False, f"Ошибка: {e}"


"""ChestReader mixin: сетка, слоты, калибровка и отладка."""
import os

import cv2

from chest_scanner.commons import save_json
from chest_scanner.image_utils import crop_relative
from chest_scanner.logging_setup import logger
from chest_scanner import paths
from chest_scanner.screenshot import screenshot_bgr

class GridMixin:
    """Methods extracted from the original ChestReader."""

    def get_grid_rect(self, screen):
        sh, sw = screen.shape[:2]
        sx, sy = sw / self.ref_w, sh / self.ref_h
        x1 = round(self.grid["x1"] * sx)
        y1 = round(self.grid["y1"] * sy)
        x2 = round(self.grid["x2"] * sx)
        y2 = round(self.grid["y2"] * sy)
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1
        return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1,
                "columns": self.grid["columns"], "rows": self.grid["rows"]}

    def get_slots(self, screen):
        grid = self.get_grid_rect(screen)
        sw = grid["width"] / grid["columns"]
        sh = grid["height"] / grid["rows"]
        slots = []
        for row in range(grid["rows"]):
            for col in range(grid["columns"]):
                x1 = round(grid["x"] + col * sw) + 1
                x2 = round(grid["x"] + (col + 1) * sw) - 1
                y1 = round(grid["y"] + row * sh) + 1
                y2 = round(grid["y"] + (row + 1) * sh) - 1
                slots.append({"row": row, "column": col,
                              "image": screen[y1:y2, x1:x2],
                              "x1": x1, "y1": y1, "x2": x2, "y2": y2})
        return slots

    def show_grid_debug(self):
        if self._grid_debug_active:
            return
        self._grid_debug_active = True
        try:
            screen = screenshot_bgr()
            sh, sw = screen.shape[:2]
            grid = self.get_grid_rect(screen)
            base = screen.copy()
            cv2.rectangle(base, (grid["x"], grid["y"]),
                          (grid["x"] + grid["width"], grid["y"] + grid["height"]),
                          (0, 0, 255), 2)
            for idx, slot in enumerate(self.get_slots(screen), 1):
                cv2.rectangle(base, (slot["x1"] - 1, slot["y1"] - 1),
                              (slot["x2"] + 1, slot["y2"] + 1), (0, 255, 0), 1)
                cv2.putText(base, str(idx), (slot["x1"] + 3, slot["y1"] + 13),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1, cv2.LINE_AA)
            sx, sy = sw / self.ref_w, sh / self.ref_h
            for i, line in enumerate([
                f"Screen: {sw}x{sh}", f"Ref: {self.ref_w}x{self.ref_h}",
                f"Scale: {sx:.2f}x{sy:.2f}",
                f"TL: ({grid['x']},{grid['y']})",
                f"BR: ({grid['x'] + grid['width']},{grid['y'] + grid['height']})",
                f"Size: {grid['width']}x{grid['height']}",
                f"Slots: {grid['columns']}x{grid['rows']}"]):
                cv2.putText(base, line, (20, 40 + i * 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1, cv2.LINE_AA)
            cv2.circle(base, (grid["x"], grid["y"]), 6, (0, 255, 0), -1)
            cv2.circle(base, (grid["x"] + grid["width"], grid["y"] + grid["height"]), 6, (255, 100, 0), -1)
            win = "Chest grid debug"
            cv2.namedWindow(win, cv2.WINDOW_NORMAL)
            cursor = {"x": None, "y": None}

            def on_mouse(ev, x, y, fl, p):
                cursor["x"], cursor["y"] = x, y

            cv2.setMouseCallback(win, on_mouse)
            while True:
                frame = base.copy()
                cx, cy = cursor["x"], cursor["y"]
                if cx is not None and cy is not None:
                    cv2.line(frame, (cx - 15, cy), (cx + 15, cy), (0, 255, 255), 1)
                    cv2.line(frame, (cx, cy - 15), (cx, cy + 15), (0, 255, 255), 1)
                    tx, ty = min(cx + 12, sw - 180), max(cy - 10, 25)
                    cv2.putText(frame, f"({cx},{cy})", (int(tx), int(ty)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
                    if 0 <= cy < sh and 0 <= cx < sw:
                        b, g, r = screen[cy, cx]
                        cv2.putText(frame, f"BGR:{b},{g},{r}", (int(tx), int(ty) + 20),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
                cv2.putText(frame, "Esc/Q/Space - close", (20, sh - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
                cv2.imshow(win, frame)
                key = cv2.waitKey(30) & 0xFF
                if key in (27, ord("q"), ord(" ")):
                    break
            cv2.destroyWindow(win)
        except Exception as e:
            logger.error(f"Grid debug: {e}", exc_info=True)
        finally:
            self._grid_debug_active = False

    def calibrate_grid_interactive(self):
        screen = screenshot_bgr()
        sh, sw = screen.shape[:2]
        columns = self.grid.get("columns", 9)
        rows = self.grid.get("rows", 6)
        state = {"p1": None, "p2": None, "phase": "first"}
        win = "Chest Grid Calibration"
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)

        def draw_preview(img, x1, y1, x2, y2):
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
            w, h = x2 - x1, y2 - y1
            if w <= 0 or h <= 0:
                return
            cw, ch = w / columns, h / rows
            for c in range(1, columns):
                px = int(x1 + c * cw)
                cv2.line(img, (px, y1), (px, y2), (0, 200, 0), 1)
            for r in range(1, rows):
                py = int(y1 + r * ch)
                cv2.line(img, (x1, py), (x2, py), (0, 200, 0), 1)
            cv2.circle(img, (x1, y1), 5, (0, 255, 0), -1)
            cv2.circle(img, (x2, y2), 5, (255, 100, 0), -1)

        def on_mouse(ev, x, y, fl, p):
            if ev != cv2.EVENT_LBUTTONDOWN:
                return
            if state["phase"] == "first":
                state["p1"] = (x, y)
                state["phase"] = "second"
            elif state["phase"] == "second":
                state["p2"] = (x, y)
                state["phase"] = "adjust"

        cv2.setMouseCallback(win, on_mouse)

        while True:
            frame = screen.copy()
            if state["phase"] == "first":
                cv2.putText(frame, "Click TOP-LEFT corner of grid", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
                cv2.putText(frame, "Esc = cancel", (20, 72),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
            elif state["phase"] == "second":
                p1 = state["p1"]
                cv2.circle(frame, p1, 5, (0, 255, 0), -1)
                cv2.putText(frame, f"TL: {p1}", (p1[0] + 10, p1[1] - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
                cv2.putText(frame, "Click BOTTOM-RIGHT corner", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
                cv2.putText(frame, "R = reset | Esc = cancel", (20, 72),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
            else:
                p1, p2 = state["p1"], state["p2"]
                x1, y1 = min(p1[0], p2[0]), min(p1[1], p2[1])
                x2, y2 = max(p1[0], p2[0]), max(p1[1], p2[1])
                draw_preview(frame, x1, y1, x2, y2)
                w, h = x2 - x1, y2 - y1
                for i, line in enumerate([
                    f"Grid: ({x1},{y1}) -> ({x2},{y2})",
                    f"Size: {w}x{h} | Cell: {w / columns:.1f}x{h / rows:.1f}",
                    f"Slots: {columns}x{rows}"]):
                    cv2.putText(frame, line, (20, 40 + i * 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1, cv2.LINE_AA)
                cv2.putText(frame, "Arrows=move | WASD=TL | IJKL=BR", (20, sh - 50),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
                cv2.putText(frame, "Enter=save | R=reset | Esc=cancel", (20, sh - 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
            cv2.imshow(win, frame)
            key = cv2.waitKey(30) & 0xFF
            if key == 27:
                cv2.destroyWindow(win)
                return None
            if key == ord("r"):
                state.update({"p1": None, "p2": None, "phase": "first"})
                continue
            if state["phase"] != "adjust":
                continue
            p1, p2 = list(state["p1"]), list(state["p2"])
            if key in (13, ord("\r")):
                x1, y1 = min(p1[0], p2[0]), min(p1[1], p2[1])
                x2, y2 = max(p1[0], p2[0]), max(p1[1], p2[1])
                scx, scy = sw / self.ref_w, sh / self.ref_h
                self.grid = {"x1": round(x1 / scx), "y1": round(y1 / scy),
                             "x2": round(x2 / scx), "y2": round(y2 / scy),
                             "columns": columns, "rows": rows}
                self.config["chest_grid"] = self.grid
                save_json(paths.CONFIG_FILE, self.config)
                cv2.destroyWindow(win)
                created = self.auto_generate_templates(force=False)
                if created:
                    logger.info(f"Калибровка: созданы шаблоны {', '.join(created)}")
                return self.grid
            if key == 82:    p1[1] -= 1; p2[1] -= 1
            elif key == 84:  p1[1] += 1; p2[1] += 1
            elif key == 81:  p1[0] -= 1; p2[0] -= 1
            elif key == 83:  p1[0] += 1; p2[0] += 1
            elif key == ord("w"): p1[1] -= 1
            elif key == ord("s"): p1[1] += 1
            elif key == ord("a"): p1[0] -= 1
            elif key == ord("d"): p1[0] += 1
            elif key == ord("i"): p2[1] -= 1
            elif key == ord("k"): p2[1] += 1
            elif key == ord("j"): p2[0] -= 1
            elif key == ord("l"): p2[0] += 1
            state["p1"], state["p2"] = tuple(p1), tuple(p2)

    def save_quantity_debug(self):
        screen = screenshot_bgr()
        dd = os.path.join(paths.RESULTS_DIR, "quantity_debug")
        os.makedirs(dd, exist_ok=True)
        for slot in self.get_slots(screen):
            si = slot["image"]
            if si is None or si.size == 0:
                continue
            ci = crop_relative(si, self.config["count_roi_in_slot"])
            if ci is None or ci.size == 0:
                continue
            binary = self.make_digit_mask(ci)
            r, c = slot["row"] + 1, slot["column"] + 1
            for sfx, img in (("original", ci), ("binary", binary)):
                lg = cv2.resize(img, None, fx=15, fy=15, interpolation=cv2.INTER_NEAREST)
                cv2.imwrite(os.path.join(dd, f"slot_r{r}_c{c}_{sfx}.png"), lg)
        logger.debug(f"Области количества: {dd}")


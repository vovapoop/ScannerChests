"""Тесты выбора источника снимка: экран или окно игры."""
import json

import numpy as np

from chest_scanner import paths, screenshot
from chest_scanner.config import validate_config


class _FakeSct:
    monitors = [{}, {"left": 0, "top": 0, "width": 4, "height": 3}]

    def grab(self, _mon):
        return np.full((3, 4, 4), 7, dtype=np.uint8)


def _reset_capture():
    screenshot.configure_capture("screen", "Minecraft")


def test_configure_capture_ignores_unknown_source():
    try:
        screenshot.configure_capture("no-such-source", "Custom")
        assert screenshot.capture_settings() == {"source": "screen",
                                                 "title": "Custom"}
    finally:
        _reset_capture()


def test_window_mode_returns_window_frame(monkeypatch):
    frame = np.full((30, 40, 3), 128, dtype=np.uint8)
    monkeypatch.setattr(screenshot, "_window_screenshot", lambda: frame)
    screenshot.configure_capture("window", "Minecraft")
    try:
        assert screenshot.screenshot_bgr() is frame
    finally:
        _reset_capture()


def test_window_mode_falls_back_to_screen(monkeypatch):
    monkeypatch.setattr(screenshot, "_window_screenshot", lambda: None)
    monkeypatch.setattr(screenshot, "_get_sct", lambda: _FakeSct())
    screenshot.configure_capture("window", "Minecraft")
    try:
        img = screenshot.screenshot_bgr()
        assert img.shape == (3, 4, 3)
    finally:
        _reset_capture()


def test_window_mode_region_is_cropped_from_window_frame(monkeypatch):
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    monkeypatch.setattr(screenshot, "_window_screenshot", lambda: frame)
    screenshot.configure_capture("window", "Minecraft")
    try:
        assert screenshot.screenshot_region_bgr(10, 20, 30, 40).shape == (40, 30, 3)
        # За границей кадра область не выходит за его пределы.
        assert screenshot.screenshot_region_bgr(190, 90, 50, 50).shape == (10, 10, 3)
    finally:
        _reset_capture()


def test_config_validates_capture_settings():
    cfg, warns = validate_config({"capture_source": "bogus", "game_window_title": "  "})
    assert cfg["capture_source"] == paths.DEFAULT_CONFIG["capture_source"]
    assert cfg["game_window_title"] == paths.DEFAULT_CONFIG["game_window_title"]
    assert any("capture_source" in w for w in warns)
    assert any("game_window_title" in w for w in warns)


def test_config_keeps_valid_capture_settings():
    cfg, _ = validate_config({"capture_source": "window",
                              "game_window_title": "Minecraft* Forge 1.20.1"})
    assert cfg["capture_source"] == "window"
    assert cfg["game_window_title"] == "Minecraft* Forge 1.20.1"


def test_config_resets_broken_grid_geometry():
    # Такой конфиг реально получался после неудачной калибровки:
    # высота сетки на весь экран при ширине 324 px.
    broken = {"x1": 798, "y1": 3, "x2": 1122, "y2": 568, "columns": 9, "rows": 6}
    cfg, warns = validate_config({"chest_grid": broken})
    assert cfg["chest_grid"] == paths.DEFAULT_CONFIG["chest_grid"]
    assert any("chest_grid" in w for w in warns)


def test_config_keeps_valid_grid():
    grid = {"x1": 798, "y1": 406, "x2": 1122, "y2": 568, "columns": 9, "rows": 3}
    cfg, _ = validate_config({"chest_grid": dict(grid)})
    assert cfg["chest_grid"] == grid


def test_reload_profile_applies_capture_source(tmp_path, monkeypatch):
    """Источник захвата из конфига применяется при загрузке профиля.

    Регрессия: configure_capture() не вызывался из reload_profile(),
    поэтому настройка «окно игры / весь экран» ни на что не влияла.
    """
    from chest_scanner.scanner.reader import ChestReader
    from chest_scanner.template_cache import template_cache

    cfg = dict(paths.DEFAULT_CONFIG)
    cfg["capture_source"] = "window"
    cfg["game_window_title"] = "Minecraft* Forge"
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
    items_file = tmp_path / "items.json"
    items_file.write_text('{"items": []}', encoding="utf-8")
    monkeypatch.setattr(paths, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(paths, "ITEMS_FILE", str(items_file))

    reader = ChestReader.__new__(ChestReader)
    reader.template_cache = template_cache
    reader.prompted_unknowns = set()
    for name in ("load_digit_templates", "_rebuild_indexes",
                 "print_startup_status"):
        monkeypatch.setattr(ChestReader, name, lambda self, *a, **k: None)

    try:
        reader.reload_profile()
        assert screenshot.capture_settings() == {
            "source": "window", "title": "Minecraft* Forge"}
    finally:
        _reset_capture()


def test_wgc_session_stops_once_after_first_frame(monkeypatch):
    """Сессию WGC останавливает управляющий поток ровно один раз."""
    import sys
    import types
    from types import SimpleNamespace

    from chest_scanner import capture

    frame = np.full((40, 60, 4), 120, dtype=np.uint8)
    control = SimpleNamespace(stop_calls=0)

    def stop():
        control.stop_calls += 1

    control.stop = stop

    class FakeCapture:
        def __init__(self, **_kwargs):
            self.handlers = {}

        def event(self, fn):
            self.handlers[fn.__name__] = fn
            return fn

        def start_free_threaded(self):
            callback = self.handlers["on_frame_arrived"]
            callback(SimpleNamespace(frame_buffer=frame), control)
            # Повторный callback не должен перезаписывать первый кадр
            # и не должен самостоятельно останавливать сессию.
            callback(SimpleNamespace(frame_buffer=frame + 10), control)
            return control

    fake_module = types.ModuleType("windows_capture")
    fake_module.WindowsCapture = FakeCapture
    monkeypatch.setitem(sys.modules, "windows_capture", fake_module)
    monkeypatch.setattr(capture, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(capture, "find_game_window", lambda _title: 123)
    monkeypatch.setattr(capture, "_crop_to_client", lambda img, _hwnd: img)

    result = capture.grab_window("Minecraft", timeout=0.1)

    assert result.shape == (40, 60, 3)
    assert np.all(result == 120)
    assert control.stop_calls == 1

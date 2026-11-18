import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from chest_scanner.gui.main_window import ScannerGUI
from chest_scanner.gui.widgets import SideBar

_APP = QApplication.instance() or QApplication([])


def _busy_window():
    messages = []
    return SimpleNamespace(
        busy=True,
        _log=lambda message, color=None: messages.append(message),
        messages=messages,
    )


def test_undo_scan_is_blocked_while_an_operation_is_running():
    window = _busy_window()

    ScannerGUI._undo_last_scan(window)

    assert window.messages
    assert "\u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438" in window.messages[0]


def test_reset_config_is_blocked_while_an_operation_is_running():
    window = _busy_window()

    ScannerGUI._reset_config(window)

    assert window.messages
    assert "\u0441\u0431\u0440\u043e\u0441\u0438\u0442\u044c \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438" in window.messages[0]


def test_import_item_database_is_blocked_while_an_operation_is_running():
    window = _busy_window()

    ScannerGUI._import_item_db(window)

    assert window.messages
    assert "\u0438\u043c\u043f\u043e\u0440\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0431\u0430\u0437\u0443" in window.messages[0]


def test_undo_button_is_disabled_when_there_is_no_scan_history():
    button = SimpleNamespace(enabled=None, setEnabled=lambda value: setattr(button, "enabled", value))
    window = SimpleNamespace(busy=False, undo_scan_btn=button, _has_scan_history=lambda: False)

    ScannerGUI._sync_undo_scan_btn(window)

    assert button.enabled is False


def test_undo_button_is_enabled_when_history_exists():
    button = SimpleNamespace(enabled=None, setEnabled=lambda value: setattr(button, "enabled", value))
    window = SimpleNamespace(busy=False, undo_scan_btn=button, _has_scan_history=lambda: True)

    ScannerGUI._sync_undo_scan_btn(window)

    assert button.enabled is True


def test_sidebar_exit_has_tooltip_and_accessible_name():
    sidebar = SideBar([], on_exit=lambda: None)
    try:
        assert sidebar.exit_btn.toolTip() == "\u0417\u0430\u043a\u0440\u044b\u0442\u044c Chest Scanner"
        assert sidebar.exit_btn.accessibleName() == "\u0412\u044b\u0445\u043e\u0434"
    finally:
        sidebar.close()


def test_undo_scan_does_not_open_confirmation_when_history_is_empty(monkeypatch):
    window = _busy_window()
    window.busy = False
    window.reader = SimpleNamespace(latest_scan=lambda: None)

    ScannerGUI._undo_last_scan(window)

    assert window.messages
    assert "\u0441\u043a\u0430\u043d\u043e\u0432" in window.messages[0]


def test_busy_state_disables_conflicting_buttons_and_sidebar_actions():
    class Button:
        def __init__(self):
            self.enabled = None

        def setEnabled(self, value):
            self.enabled = value

    names = ("scan_btn", "items_btn", "discord_btn", "tour_btn", "auto_scan_btn")
    buttons = {name: Button() for name in names}
    sidebar_buttons = {key: Button() for key in ("settings", "profiles", "data", "history", "help")}
    window = SimpleNamespace(
        busy=True,
        **buttons,
        _sidebar=SimpleNamespace(buttons=sidebar_buttons),
        _sync_undo_scan_btn=lambda: None,
    )

    ScannerGUI._sync_action_state(window)

    assert all(button.enabled is False for button in buttons.values())
    assert all(sidebar_buttons[key].enabled is False for key in ("settings", "profiles", "data"))
    assert sidebar_buttons["history"].enabled is None
    assert sidebar_buttons["help"].enabled is None


def test_ready_state_reenables_conflicting_buttons_and_sidebar_actions():
    class Button:
        def __init__(self):
            self.enabled = None

        def setEnabled(self, value):
            self.enabled = value

    names = ("scan_btn", "items_btn", "discord_btn", "tour_btn", "auto_scan_btn")
    buttons = {name: Button() for name in names}
    sidebar_buttons = {key: Button() for key in ("settings", "profiles", "data")}
    window = SimpleNamespace(
        busy=False,
        **buttons,
        _sidebar=SimpleNamespace(buttons=sidebar_buttons),
        _sync_undo_scan_btn=lambda: None,
    )

    ScannerGUI._sync_action_state(window)

    assert all(button.enabled is True for button in buttons.values())
    assert all(button.enabled is True for button in sidebar_buttons.values())


def test_open_item_database_is_blocked_while_busy():
    window = _busy_window()

    ScannerGUI.open_item_database(window)

    assert window.messages and len(window.messages[0]) > 10


def test_open_settings_is_blocked_while_busy():
    window = _busy_window()

    ScannerGUI.open_settings(window)

    assert window.messages and len(window.messages[0]) > 10


def test_auto_scan_toggle_is_blocked_while_busy():
    window = _busy_window()

    ScannerGUI._toggle_auto_scan(window)

    assert window.messages and len(window.messages[0]) > 10

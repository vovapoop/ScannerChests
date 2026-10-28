"""Сборка ScannerGUI из специализированных миксинов."""
from chest_scanner.gui.core import GuiCoreMixin
from chest_scanner.gui.dialogs import GuiDialogsMixin
from chest_scanner.gui.settings import GuiSettingsMixin
from chest_scanner.gui.widgets import GuiWidgetsMixin


class ScannerGUI(GuiCoreMixin, GuiWidgetsMixin, GuiSettingsMixin, GuiDialogsMixin):
    """Публичный API GUI. Поведение унаследовано без изменений."""

    pass

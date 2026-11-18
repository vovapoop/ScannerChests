"""Проверки актуальности встроенной справки."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_help_dialog_contains_current_workflows():
    from PySide6.QtWidgets import QApplication

    from chest_scanner.gui.help import HelpDialog

    QApplication.instance() or QApplication([])
    dialog = HelpDialog()
    text = dialog.browser.toPlainText()

    assert "Неизвестные предметы" in text
    assert "История и статистика" in text
    assert "Профили" in text
    assert "64" in text and "16" in text
    assert "количеством 1 отображается без цифры" in text
    assert "более ранний результат" in text
    assert "теги" not in text.casefold()
    assert "алиасы" not in text.casefold()
    assert "синонимы" not in text.casefold()
    dialog.close()


def test_help_dialog_has_section_navigation():
    from PySide6.QtWidgets import QApplication

    from chest_scanner.gui.help import HelpDialog

    QApplication.instance() or QApplication([])
    dialog = HelpDialog()
    html = dialog.browser.toHtml()

    for anchor in ("#quick", "#items", "#unknown", "#history", "#profiles", "#troubleshooting"):
        assert anchor in html
    dialog.close()



def test_help_hotkey_instructions_do_not_list_discord():
    from PySide6.QtWidgets import QApplication

    from chest_scanner.gui.help import HelpDialog

    QApplication.instance() or QApplication([])
    dialog = HelpDialog()
    text = dialog.browser.toPlainText()
    assert "сканирование и выход" in text
    assert "сканирование, Discord, выход" not in text
    dialog.close()


def test_disabled_accent_buttons_have_a_disabled_visual_style():
    from chest_scanner.gui.theme import build_stylesheet

    stylesheet = build_stylesheet()
    assert "QPushButton#accent:disabled" in stylesheet
    assert "background: #171b22;" in stylesheet
    assert "color: #8f9aaa;" in stylesheet

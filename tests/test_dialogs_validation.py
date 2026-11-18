import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from chest_scanner.gui.dialogs import FirstRunWizard, HistoryDialog, UnknownItemDialog

_APP = QApplication.instance() or QApplication([])


def test_unknown_item_requires_a_name():
    dialog = UnknownItemDialog("unknown", "")
    try:
        assert not dialog.add_btn.isEnabled()
        dialog.accept()
        assert dialog.result() == 0
        dialog.name_edit.setText("Diamond")
        assert dialog.add_btn.isEnabled()
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.item_name() == "Diamond"
    finally:
        dialog.close()


def test_history_date_filter_rejects_partial_date(monkeypatch):
    assert HistoryDialog._parse_date("02.11.2026") is not None
    assert HistoryDialog._parse_date("02.11") is None
    monkeypatch.setattr("chest_scanner.gui.dialogs.load_all_chests", lambda: [
        {"timestamp": "02.11.2026 10:00:00", "totals": {"Diamond": 2}},
        {"timestamp": "not-a-date", "totals": {"Stone": 1}},
    ])
    dialog = HistoryDialog()
    try:
        dialog.from_edit.setText("02.11")
        dialog._refresh_list()
        assert dialog._filtered == []
        assert dialog.total_lbl.text().startswith("\u041d\u0435\u0432")
        dialog.from_edit.setText("02.11.2026")
        dialog._refresh_list()
        assert len(dialog._filtered) == 1
        assert dialog._filtered[0]["totals"] == {"Diamond": 2}
    finally:
        dialog.close()


def test_first_run_rejects_invalid_webhook_before_saving(monkeypatch):
    class Reader:
        config = {}

        @staticmethod
        def get_discord_webhook_url():
            return ""

        @staticmethod
        def reload_profile():
            raise AssertionError("invalid webhook must be rejected before profile activation")

    messages = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: messages.append(args))
    wizard = FirstRunWizard(Reader())
    try:
        wizard.webhook_edit.setText("not-a-webhook")
        wizard._finish()
        assert messages
        assert "Webhook" in messages[0][2]
        assert not wizard.result() == QDialog.DialogCode.Accepted
    finally:
        wizard.close()

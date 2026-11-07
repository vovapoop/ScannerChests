"""Точка входа приложения: инициализация читателя и запуск GUI."""
import faulthandler
import os

from chest_scanner.gui import ScannerGUI
from chest_scanner.logging_setup import LOGS_DIR
from chest_scanner.scanner.reader import ChestReader

# Нативные сбои (обращение к Qt из чужого потока и т.п.) не дают
# Python-трейсбека; faulthandler пишет стек потока-виновника в файл.
try:
    _crash_stream = open(os.path.join(LOGS_DIR, "crash.log"), "a",
                         encoding="utf-8")
    faulthandler.enable(_crash_stream)
except OSError:
    faulthandler.enable()


def main():
    reader = ChestReader()
    app = ScannerGUI(reader)
    app.run()

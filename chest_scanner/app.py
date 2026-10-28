"""Точка входа приложения: инициализация читателя и запуск GUI."""
from chest_scanner.gui import ScannerGUI
from chest_scanner.scanner.reader import ChestReader


def main():
    reader = ChestReader()
    app = ScannerGUI(reader)
    app.run()

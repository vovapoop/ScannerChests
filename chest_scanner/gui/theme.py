"""Тема оформления GUI: палитра цветов и QSS-стили.

Вся стилизация приложения строится здесь: единый тёмный стиль,
переиспользуемые объектные имена (#accent, #danger, #ghost, .card и т.д.).
"""

PALETTE = {
    "BG": "#101318",
    "PANEL": "#171b22",
    "PANEL_2": "#1d232c",
    "PANEL_3": "#232b37",
    "BORDER": "#2a313c",
    "TEXT": "#f2f4f7",
    "MUTED": "#8f9aaa",
    "ACCENT": "#5865f2",
    "ACCENT_HOVER": "#6975ff",
    "GREEN": "#35c98b",
    "RED": "#f25f67",
    "YELLOW": "#f0b84b",
    "SELECT": "#29304a",
}

FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"


def build_stylesheet(palette=None):
    """Собирает QSS-строку из палитры (можно переопределить отдельные цвета)."""
    p = dict(PALETTE)
    if palette:
        p.update(palette)
    bg, panel, panel2, panel3 = p["BG"], p["PANEL"], p["PANEL_2"], p["PANEL_3"]
    border, text, muted = p["BORDER"], p["TEXT"], p["MUTED"]
    accent, accent_h = p["ACCENT"], p["ACCENT_HOVER"]
    red, sel = p["RED"], p["SELECT"]
    ff, mf = FONT_FAMILY, MONO_FAMILY

    return f"""
* {{
    font-family: "{ff}";
    font-size: 13px;
    outline: none;
}}
QMainWindow, QDialog {{
    background: {bg};
}}
QWidget {{
    color: {text};
    background: transparent;
}}
QLabel {{
    background: transparent;
    color: {text};
}}
QLabel[muted="true"] {{
    color: {muted};
}}
QLabel[small="true"] {{
    font-size: 11px;
}}
QLabel[big="true"] {{
    font-size: 22px;
    font-weight: 700;
}}
QLabel[title="true"] {{
    font-size: 24px;
    font-weight: 700;
}}
QLabel[section="true"] {{
    font-size: 11px;
    font-weight: 700;
    color: {muted};
    letter-spacing: 1px;
}}
QFrame[card="true"] {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 10px;
}}
QFrame#headerBar {{
    background: {bg};
    border: none;
}}
QPushButton {{
    background: {panel2};
    color: {text};
    border: 1px solid {border};
    border-radius: 7px;
    padding: 7px 16px;
}}
QPushButton:hover {{
    background: {panel3};
    border-color: #3a4455;
}}
QPushButton:pressed {{
    background: {sel};
}}
QPushButton:disabled {{
    color: {muted};
    background: {panel};
}}
QPushButton#accent {{
    background: {accent};
    border: 1px solid {accent};
    color: white;
    font-weight: 600;
}}
QPushButton#accent:hover {{
    background: {accent_h};
    border-color: {accent_h};
}}
QPushButton#accent:pressed {{
    background: {sel};
    border-color: {accent_h};
}}
QPushButton#danger {{
    background: transparent;
    color: {red};
    border: 1px solid {red};
}}
QPushButton#danger:hover {{
    background: rgba(242, 95, 103, 0.12);
}}
QPushButton#ghost {{
    background: transparent;
    border: none;
    color: {muted};
    text-align: left;
    padding: 8px 12px;
    border-radius: 7px;
}}
QPushButton#ghost:hover {{
    color: {text};
    background: {panel2};
}}
QPushButton#ghost:checked {{
    background: {accent};
    color: white;
    font-weight: 600;
}}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
    background: {panel2};
    border: 1px solid {border};
    border-radius: 7px;
    padding: 6px 10px;
    selection-background-color: {accent};
    selection-color: white;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {accent};
}}
QLineEdit[mono="true"] {{
    font-family: "{mf}";
    font-size: 12px;
}}
QComboBox {{
    background: {panel2};
    border: 1px solid {border};
    border-radius: 7px;
    padding: 6px 10px;
}}
QComboBox:hover {{
    border-color: #3a4455;
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {muted};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {panel2};
    border: 1px solid {border};
    border-radius: 7px;
    selection-background-color: {sel};
    selection-color: {text};
    padding: 4px;
}}
QListWidget {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 9px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 8px 12px;
    border-radius: 6px;
}}
QListWidget::item:hover {{
    background: {panel3};
}}
QListWidget::item:selected {{
    background: {sel};
    color: {text};
}}
QListWidget#navList {{
    background: transparent;
    border: none;
    padding: 2px;
}}
QListWidget#navList::item {{
    padding: 10px 14px;
    border-radius: 7px;
}}
QListWidget#navList::item:selected {{
    background: {accent};
    color: white;
    font-weight: 600;
}}
QTableWidget, QTableView {{
    background: {panel};
    alternate-background-color: {panel2};
    border: 1px solid {border};
    border-radius: 9px;
    gridline-color: {border};
}}
QTableWidget::item, QTableView::item {{
    padding: 4px 6px;
    border: none;
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {sel};
    color: {text};
}}
QHeaderView::section {{
    background: {panel2};
    color: {muted};
    border: none;
    border-bottom: 1px solid {border};
    padding: 9px 8px;
    font-weight: 600;
    font-size: 12px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {panel3};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: #33404f;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {panel3};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border: 2px solid {border};
    border-radius: 5px;
    background: {panel2};
}}
QCheckBox::indicator:hover {{
    border-color: {accent};
}}
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
}}
QRadioButton {{
    spacing: 8px;
}}
QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid {border};
    border-radius: 8px;
    background: {panel2};
}}
QRadioButton::indicator:checked {{
    background: {accent};
    border-color: {accent};
}}
QSlider::groove:horizontal {{
    background: {panel2};
    height: 4px;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {accent};
    width: 15px;
    margin: -6px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{
    background: {accent_h};
}}
QMenu {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    padding: 7px 26px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background: {sel};
}}
QMenu::separator {{
    height: 1px;
    background: {border};
    margin: 5px 8px;
}}
QToolTip {{
    background: {panel3};
    color: {text};
    border: 1px solid {border};
    padding: 6px 8px;
    border-radius: 5px;
}}
QMessageBox {{
    background: {bg};
}}
QMessageBox QLabel {{
    color: {text};
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QSplitter::handle {{
    background: {border};
    width: 1px;
}}
QProgressBar {{
    background: {panel2};
    border: 1px solid {border};
    border-radius: 5px;
    text-align: center;
    color: {muted};
    height: 16px;
}}
QProgressBar::chunk {{
    background: {accent};
    border-radius: 4px;
}}
QPlainTextEdit#logView {{
    background: {panel};
    border: none;
    font-family: "{mf}";
    font-size: 12px;
    padding: 8px;
}}
QStatusBar {{
    background: {panel};
    color: {muted};
}}
"""
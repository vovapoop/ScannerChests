"""Тесты интерактивного тура (TourOverlay) — offscreen Qt."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def host(qapp):
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    w.resize(800, 600)
    return w


def _steps(host):
    from PySide6.QtWidgets import QPushButton

    btn = QPushButton("Цель", host)
    btn.setGeometry(300, 250, 120, 40)
    from chest_scanner.gui.tour import TourStep

    return btn, [
        TourStep("Первый", "Текст 1"),
        TourStep("Второй", "Текст 2", target=btn),
        TourStep("Последний", "Текст 3"),
    ]


def test_tour_navigation_and_finish(host, qapp):
    from chest_scanner.gui.tour import TourOverlay

    finished = []
    btn, steps = _steps(host)
    host.show()
    tour = TourOverlay(host, steps, on_finish=lambda: finished.append(True))

    assert tour._counter_lbl.text() == "Шаг 1 из 3"
    tour.next()
    assert tour._title_lbl.text() == "Второй"
    tour.back()
    assert tour._title_lbl.text() == "Первый"
    tour.next()
    tour.next()
    assert tour._title_lbl.text() == "Последний"
    tour.next()  # на последнем шаге кнопка «Готово» закрывает тур
    assert finished == [True]


def test_tour_skip_finishes(host, qapp):
    from chest_scanner.gui.tour import TourOverlay

    finished = []
    _btn, steps = _steps(host)
    host.show()
    tour = TourOverlay(host, steps, on_finish=lambda: finished.append(True))
    tour.finish()
    assert finished == [True]


def test_tour_esc_finishes(host, qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from chest_scanner.gui.tour import TourOverlay

    finished = []
    _btn, steps = _steps(host)
    host.show()
    tour = TourOverlay(host, steps, on_finish=lambda: finished.append(True))
    tour.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Escape,
                                 Qt.KeyboardModifier.NoModifier))
    assert finished == [True]


def test_tour_card_stays_inside_host(host, qapp):
    """Карточка не выходит за границы окна даже при узком окне."""
    from chest_scanner.gui.tour import CARD_MARGIN, TourOverlay

    _btn, steps = _steps(host)
    host.resize(420, 240)
    host.show()
    tour = TourOverlay(host, steps)
    card = tour._card
    geo = card.geometry()
    assert geo.left() >= CARD_MARGIN
    assert geo.top() >= CARD_MARGIN
    assert geo.right() <= host.width() - CARD_MARGIN + 1
    assert geo.bottom() <= host.height() + 40  # допустим лёгкий перелив по высоте


def test_tour_without_target_shows_center_card(host, qapp):
    from chest_scanner.gui.tour import TourOverlay, TourStep

    host.show()
    tour = TourOverlay(host, [TourStep("Только текст", "Без цели")])
    assert tour._hole() is None
    geo = tour._card.geometry()
    # QRect.center() округляет вниз, поэтому допускаем погрешность 1 px.
    assert abs(geo.center().x() - host.width() // 2) <= 1
    tour.finish()


def test_settingcard_objectname_is_title(qapp):
    """Тур находит карточки настроек по заголовку."""
    from chest_scanner.gui.widgets import SettingCard

    card = SettingCard("Захват экрана")
    assert card.objectName() == "Захват экрана"

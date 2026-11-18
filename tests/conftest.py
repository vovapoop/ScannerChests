"""Shared test fixtures for deterministic GUI teardown."""

import sys

import pytest


@pytest.fixture(autouse=True)
def cleanup_top_level_qt_widgets():
    """Destroy test-created top-level widgets before the next test.

    QWidget.close() only hides a window by default. Leaving those C++ objects
    alive until interpreter shutdown can make PySide6 crash while finalizing
    its wrappers, so explicitly process DeferredDelete events after each test.
    """
    yield

    widgets_module = sys.modules.get("PySide6.QtWidgets")
    core_module = sys.modules.get("PySide6.QtCore")
    if widgets_module is None or core_module is None:
        return

    app = widgets_module.QApplication.instance()
    if app is None:
        return

    try:
        widgets = list(app.topLevelWidgets())
    except RuntimeError:
        return

    for widget in widgets:
        try:
            widget.close()
            widget.deleteLater()
        except RuntimeError:
            # The widget may already have been destroyed by its parent.
            continue

    try:
        deferred_delete = core_module.QEvent.Type.DeferredDelete
        core_module.QCoreApplication.sendPostedEvents(None, deferred_delete)
        app.processEvents()
        core_module.QCoreApplication.sendPostedEvents(None, deferred_delete)
    except RuntimeError:
        # QApplication may already be shutting down during interpreter exit.
        pass

"""Concrete scanner composed from focused mixins."""
from chest_scanner.scanner.base import ScannerBaseMixin
from chest_scanner.scanner.detection import DetectionMixin
from chest_scanner.scanner.ocr import OcrMixin
from chest_scanner.scanner.grid import GridMixin
from chest_scanner.scanner.items import ItemsMixin
from chest_scanner.scanner.workflow import WorkflowMixin
from chest_scanner.scanner.discord import DiscordMixin


class ChestReader(
    ScannerBaseMixin,
    DetectionMixin,
    OcrMixin,
    GridMixin,
    ItemsMixin,
    WorkflowMixin,
    DiscordMixin,
):
    """Public ChestReader API; behavior is inherited unchanged from the original class."""

    pass

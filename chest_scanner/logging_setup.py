"""Logging configuration and time utilities."""
import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

from chest_scanner.app_bootstrap import BASE_DIR_FOR_APP, ZoneInfo

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
LOGS_DIR = os.path.join(BASE_DIR_FOR_APP, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

logger = logging.getLogger("ChestScanner")
logger.setLevel(logging.DEBUG)

_fh = RotatingFileHandler(os.path.join(LOGS_DIR, "scanner.log"),
                          maxBytes=1_000_000, backupCount=5, encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)

_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
logger.addHandler(_ch)


def now_moscow():
    if ZoneInfo:
        try:
            return datetime.now(ZoneInfo("Europe/Moscow"))
        except Exception:
            pass
    return datetime.now()

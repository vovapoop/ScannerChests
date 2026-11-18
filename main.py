"""Точка входа Chest Scanner: бутстрап .venv, затем запуск приложения."""
import os

from chest_scanner.app_bootstrap import (
    _ensure_venv,
    _startup_error,
    take_bootstrap_instance_lock,
)

if __name__ == '__main__':
    try:
        _ensure_venv()
        _instance_lock = take_bootstrap_instance_lock()
        if _instance_lock is None:
            from chest_scanner.instance_lock import acquire_instance_lock
            _instance_lock = acquire_instance_lock()
        if _instance_lock is None:
            if os.name == "nt":
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "ChestScanner ??? ???????. ???????? ???????? ???? ????? ???????? ??? ?????? ??????????.",
                    "ChestScanner", 0x40)
            else:
                print("ChestScanner is already running.")
            raise SystemExit(0)
        from chest_scanner.config import recover_profile_operations
        recover_profile_operations()
        from chest_scanner.app import main
        main()
    except Exception as e:
        _startup_error(e)
        raise

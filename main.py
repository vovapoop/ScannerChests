"""Точка входа Chest Scanner: бутстрап .venv, затем запуск приложения."""
from chest_scanner.app_bootstrap import _ensure_venv, _startup_error

if __name__ == '__main__':
    try:
        _ensure_venv()
        from chest_scanner.app import main
        main()
    except Exception as e:
        _startup_error(e)
        raise

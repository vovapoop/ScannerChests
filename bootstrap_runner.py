"""Minimal pre-import launcher used to transfer the application instance lock."""
import importlib
import importlib.util
import os
import runpy
import sys
import traceback

_HANDSHAKE_ENV = "CHESTSCANNER_BOOTSTRAP_HANDSHAKE"


def _load_instance_lock_module():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(project_dir, "chest_scanner", "instance_lock.py")
    spec = importlib.util.spec_from_file_location("_chestscanner_bootstrap_lock", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load ChestScanner instance-lock implementation.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_startup_error(app_dir, error):
    """Persist failures that happen before the regular app bootstrap is importable."""
    log_path = os.path.join(app_dir, "startup_error.log")
    detail = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    try:
        with open(log_path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(detail if detail.strip() else f"{type(error).__name__}: {error}")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        pass


def main():
    if os.environ.pop(_HANDSHAKE_ENV, None) != "1":
        raise RuntimeError("Bootstrap runner requires a parent handshake.")
    if len(sys.argv) < 2:
        raise RuntimeError("Bootstrap runner requires the application entry script.")

    entry_script = os.path.abspath(sys.argv[1])
    app_args = sys.argv[2:]
    app_dir = os.path.dirname(entry_script)
    lock_module = _load_instance_lock_module()
    instance_lock = lock_module.acquire_instance_lock(app_dir, blocking=False)
    if instance_lock is None:
        print("BUSY", flush=True)
        return 0

    try:
        print("CLAIMED", flush=True)
        if sys.stdin.readline().strip() != "COMMIT":
            return 0

        # The parent only needs the control pipe until COMMIT. Detach it before
        # application startup so later GUI output cannot fill a dead pipe.
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            try:
                stream.close()
            except OSError:
                pass
        sys.stdin = open(os.devnull, "r", encoding="utf-8")
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
        sys.stderr = sys.stdout

        # Import the full package only after the instance lock is secured.
        # Package __init__ runs before app_bootstrap and can fail before its
        # own startup-error handler is available, so persist that failure here.
        try:
            importlib.import_module("chest_scanner")
        except Exception as exc:
            _write_startup_error(app_dir, exc)
            raise
        try:
            import chest_scanner.app_bootstrap as bootstrap
            bootstrap._BOOTSTRAP_INSTANCE_LOCK = instance_lock
            instance_lock = None
            sys.argv = [entry_script, *app_args]
            runpy.run_path(entry_script, run_name="__main__")
        except Exception as exc:
            # Also cover failures importing app_bootstrap or executing the
            # entry script before main.py's own startup handler is active.
            _write_startup_error(app_dir, exc)
            raise
    finally:
        if instance_lock is not None:
            instance_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

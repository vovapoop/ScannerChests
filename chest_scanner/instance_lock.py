"""Cross-process single-instance guard for one ChestScanner installation."""
import errno
import os
import stat
import time


class InstanceLock:
    """Keep the OS file lock alive for the lifetime of the owning process."""

    def __init__(self, file_handle):
        self._file_handle = file_handle

    def close(self):
        if self._file_handle is None:
            return
        file_handle = self._file_handle
        self._file_handle = None
        try:
            if os.name == "nt":
                import msvcrt
                file_handle.seek(0)
                msvcrt.locking(file_handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
        finally:
            file_handle.close()


def _open_lock_file(lock_path):
    """Open only the regular lock-file entry, rejecting links and path swaps."""
    try:
        before = os.lstat(lock_path)
    except FileNotFoundError:
        before = None
    if before is not None and not stat.S_ISREG(before.st_mode):
        raise OSError("Instance lock path is not a regular file.")

    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        current = os.lstat(lock_path)
        if (not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                or (before is not None
                    and (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino))):
            raise OSError("Instance lock file changed while opening.")
        return os.fdopen(descriptor, "r+b")
    except BaseException:
        os.close(descriptor)
        raise


def acquire_instance_lock(base_dir=None, lock_name=".chestscanner-instance.lock", blocking=False):
    """Return a lifetime lock; optionally wait for another owner to release it."""
    if (not isinstance(lock_name, str) or not lock_name
            or os.path.basename(lock_name) != lock_name or lock_name in {".", ".."}):
        raise ValueError("Lock name must be a plain filename.")
    base_dir = os.path.abspath(base_dir or os.path.dirname(os.path.dirname(__file__)))
    os.makedirs(base_dir, exist_ok=True)
    lock_path = os.path.join(base_dir, lock_name)
    while True:
        lock_file = _open_lock_file(lock_path)
        try:
            if os.name == "nt":
                import msvcrt
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                if os.fstat(lock_file.fileno()).st_size == 0:
                    lock_file.seek(0)
                    lock_file.write(b"0")
                    lock_file.flush()
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock_file.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                if blocking:
                    time.sleep(0.05)
                    continue
                return None
            raise
        return InstanceLock(lock_file)
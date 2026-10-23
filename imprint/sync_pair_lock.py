"""One automatic-sync owner per data directory, released on process exit."""
import os
from contextlib import contextmanager
from . import config


@contextmanager
def owner():
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / 'sync-pairs.lock', 'a+b') as file:
        # Windows byte-range locks also deny reads of that byte by another
        # opener. Inspect the size without reading a currently locked byte.
        if os.fstat(file.fileno()).st_size == 0:
            file.write(b'0')
            file.flush()
        file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            if os.name == 'nt':
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file, fcntl.LOCK_UN)

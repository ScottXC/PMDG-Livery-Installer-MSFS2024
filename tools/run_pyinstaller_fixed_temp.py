from __future__ import annotations

import os
import runpy
import sys
import tempfile


_original_mkdir = os.mkdir


def mkdir_with_usable_permissions(path, mode=0o777, *, dir_fd=None):
    if dir_fd is None:
        return _original_mkdir(path, 0o777)
    return _original_mkdir(path, 0o777, dir_fd=dir_fd)


os.mkdir = mkdir_with_usable_permissions
tempfile._os.mkdir = mkdir_with_usable_permissions  # type: ignore[attr-defined]

sys.argv = ["pyinstaller", *sys.argv[1:]]
runpy.run_module("PyInstaller.__main__", run_name="__main__")

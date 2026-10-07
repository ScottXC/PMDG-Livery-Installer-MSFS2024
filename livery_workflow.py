"""Cancellable file operations and recoverable package transactions.

This module deliberately has no GUI dependencies. A worker owns an operation;
the GUI receives progress through a queue and never shares Tk objects with it.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


class InstallerError(RuntimeError):
    """A failure with an actionable message for the user."""


class OperationCancelled(InstallerError):
    pass


@dataclass
class OperationControl:
    cancelled: threading.Event = field(default_factory=threading.Event)
    notify: Callable[[str], None] = lambda _message: None
    _last_update: float = 0


_control = contextvars.ContextVar("operation_control", default=None)


@contextmanager
def operation_context(control: OperationControl):
    token = _control.set(control)
    try:
        yield
    finally:
        _control.reset(token)


def checkpoint(message: str = "", *, force: bool = False) -> None:
    control = _control.get()
    if control is None:
        return
    if control.cancelled.is_set():
        raise OperationCancelled("Cancelled. The active livery package was not changed.")
    now = time.monotonic()
    if message and (force or now - control._last_update >= 0.1):
        control.notify(message)
        control._last_update = now


def linked(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def plain_tree(root: Path) -> None:
    """Reject nested links before any copying, deletion, or recursive scan."""
    if not root.exists():
        return
    if linked(root):
        raise InstallerError(f"Linked file/folder is not supported inside a package: {root}")
    if not root.is_dir():
        return
    for current, dirs, files in os.walk(root, onerror=_raise_walk_error):
        checkpoint(f"Checking files: {Path(current).name}")
        for name in dirs + files:
            path = Path(current) / name
            if linked(path):
                raise InstallerError(f"Linked file/folder is not supported inside a package: {path}")


def _raise_walk_error(error):
    raise error


def inventory(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    result = {}
    for current, dirs, files in os.walk(root, onerror=_raise_walk_error):
        checkpoint(f"Checking package: {Path(current).name}")
        for name in dirs + files:
            path = Path(current) / name
            if linked(path):
                raise InstallerError(f"Linked file/folder is not supported inside a package: {path}")
            stat = path.stat()
            result[path.relative_to(root).as_posix()] = (stat.st_size if path.is_file() else -1, stat.st_mtime_ns)
    return result


def copy_file(source: Path, destination: Path) -> None:
    """Copy in chunks, then compare hashes before the staged file can go live."""
    source, destination = Path(source), Path(destination)
    checkpoint(f"Copying: {source.name}")
    if linked(source):
        raise InstallerError(f"Cannot copy a linked file: {source}")
    before = source.stat()
    digest = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as incoming, destination.open("wb") as outgoing:
        while block := incoming.read(1024 * 1024):
            checkpoint(f"Copying: {source.name}")
            digest.update(block)
            outgoing.write(block)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise InstallerError(f"Source changed while copying; retry when it is no longer being edited: {source}")
    actual = hashlib.sha256()
    with destination.open("rb") as copied:
        while block := copied.read(1024 * 1024):
            checkpoint(f"Verifying copy: {source.name}")
            actual.update(block)
    if digest.digest() != actual.digest():
        raise InstallerError(f"Copied file failed verification: {source}")
    shutil.copystat(source, destination)


def copy_tree(source: Path, destination: Path) -> None:
    plain_tree(source)
    shutil.copytree(source, destination, dirs_exist_ok=True, copy_function=copy_file)


def write_json(path: Path, value) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def backup_directory(target: Path) -> Path:
    return target.parent / ".pmdg-livery-backups" / target.name


def process_running(pid: int) -> bool | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            result = wintypes.DWORD()
            return result.value == 259 if kernel.GetExitCodeProcess(handle, ctypes.byref(result)) else None
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


class PackageTransaction:
    """Prepare and verify a complete copy; retain the old tree when publishing.

    The lock covers cooperating app instances. An inventory check also detects
    ordinary external edits before commit. The two directory renames are short
    but not crash-atomic; recovery.json records the original package location.
    """

    def __init__(self, target: Path, action: str):
        self.target = target.resolve()
        self.action = action
        self.stage = self.target.parent / f".pmdg-stage-{uuid.uuid4().hex}"
        self.lock = self.target.parent / f".{self.target.name}.pmdg-lock"
        self.snapshot: Path | None = None
        self.committed = False
        self._locked = False

    def __enter__(self):
        if self.target.exists() and not self.target.is_dir():
            raise InstallerError(f"Target is not a directory: {self.target}")
        try:
            self.lock.mkdir()
        except FileExistsError as exc:
            owner = self.lock / "owner.json"
            try:
                pid = json.loads(owner.read_text(encoding="utf-8"))["pid"]
                stale = type(pid) is int and process_running(pid) is False
            except (OSError, ValueError, KeyError, TypeError):
                stale = False
            if stale and not linked(self.lock):
                owner.unlink()
                self.lock.rmdir()
                self.lock.mkdir()
            else:
                raise InstallerError(
                    f"This package is busy, or an earlier operation was interrupted. "
                    f"Close other installer instances and inspect backups before removing the stale lock: {self.lock}"
                ) from exc
        self._locked = True
        try:
            write_json(self.lock / "owner.json", {"pid": os.getpid()})
            if not self.target.exists() and self.action != "restore backup":
                recoverable = list(backup_directory(self.target).glob("*/package"))
                if recoverable:
                    raise InstallerError(f"The active package is missing, but a recovery backup exists. Use Restore Backup first: {recoverable[-1].parent}")
            plain_tree(self.target)
            self.existed = self.target.exists()
            self.original = inventory(self.target)
            required = sum(size for size, _ in self.original.values() if size > 0)
            if shutil.disk_usage(self.target.parent).free < required + 16 * 1024 * 1024:
                raise InstallerError("Not enough disk space to stage the existing livery package. Free space and retry.")
            checkpoint("Preparing recoverable package copy", force=True)
            self.stage.mkdir()
            if self.existed:
                copy_tree(self.target, self.stage)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def commit(self) -> Path | None:
        checkpoint("Checking for changes before saving", force=True)
        if self.target.exists() != self.existed or inventory(self.target) != self.original:
            raise InstallerError("The livery package changed during this operation. No changes were applied; retry after other tools finish.")
        if self.existed:
            stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            record = backup_directory(self.target) / stamp
            # Refuse a redirected backup destination, including ancestors.
            for ancestor in (record.parent, record.parent.parent):
                if ancestor.exists() and linked(ancestor):
                    raise InstallerError(f"Backup location must not be linked: {ancestor}")
            record.mkdir(parents=True)
            self.snapshot = record / "package"
            write_json(record / "recovery.json", {
                "format": 1, "target": str(self.target), "action": self.action,
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note": "Complete package before this operation. Restore through the application.",
            })
        checkpoint("Saving verified package (finishing safely)", force=True)
        # From here to publication there are no cancellation points.
        if self.snapshot:
            self.target.rename(self.snapshot)
        try:
            self.stage.rename(self.target)
        except OSError as exc:
            if self.snapshot:
                try:
                    self.snapshot.rename(self.target)
                except OSError as rollback_error:
                    raise InstallerError(
                        f"Could not publish or restore the package. Your original files are retained at {self.snapshot}. "
                        f"Restore that backup after closing MSFS. Details: {rollback_error}"
                    ) from exc
            raise InstallerError(f"Could not save the package; original files were restored. Close MSFS or other tools and retry: {exc}") from exc
        self.committed = True
        return self.snapshot.parent if self.snapshot else None

    def __exit__(self, _type, _value, _traceback):
        if self.stage.exists():
            # This exact stage directory was created by this transaction.
            if self.stage.parent == self.target.parent and self.stage.name.startswith(".pmdg-stage-"):
                shutil.rmtree(self.stage, ignore_errors=True)
        if not self.committed and self.snapshot and not self.snapshot.exists():
            # Failed publication with a successful rollback has no backup tree.
            # Do not present its empty record as a usable recovery point.
            try:
                (self.snapshot.parent / "recovery.json").unlink(missing_ok=True)
                self.snapshot.parent.rmdir()
            except OSError:
                pass
        if self._locked:
            try:
                (self.lock / "owner.json").unlink(missing_ok=True)
                self.lock.rmdir()
            except OSError:
                pass

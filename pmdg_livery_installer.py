#!/usr/bin/env python3
"""
PMDG Livery Installer for MSFS 2024.

Installs and manages MSFS 2024 PMDG livery ZIP/folder packages without PMDG
OC3, using verified companion-package transactions and recovery backups.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
import queue
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable

from livery_workflow import (
    InstallerError, OperationCancelled, OperationControl, PackageTransaction,
    backup_directory, checkpoint, copy_file, copy_tree, inventory, linked,
    operation_context, plain_tree, write_json,
)


WINDOWS_FILETIME_EPOCH_OFFSET = 11644473600
ROOT_EXCLUDE_NAMES = {
    "layout.json",
    "manifest.json",
    "msfslayoutgenerator.exe",
}
WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
THUMBNAIL_STEMS = {
    "thumbnail",
    "thumbnail_small",
    "thumbnaillarge",
    "thumbnail_large",
    "thumbnail_small_0",
}
THUMBNAIL_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ppm", ".pgm"}

KNOWN_PMDG_AIRCRAFT_FOLDERS = {
    "pmdg-aircraft-736": "PMDG 737-600",
    "pmdg-aircraft-737": "PMDG 737-700",
    "pmdg-aircraft-738": "PMDG 737-800",
    "pmdg-aircraft-739": "PMDG 737-900",
    "pmdg-aircraft-77w": "PMDG 777-300ER",
}


@dataclass
class InstallReport:
    package_root: Path
    source_package_root: Path | None = None
    copied_files: int = 0
    copied_dirs: int = 0
    layout_entries: int = 0
    manifest_updated: bool = False
    backup_path: Path | None = None
    installed_roots: list[Path] = field(default_factory=list)
    recovery_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DetectedPaths:
    community_paths: list[Path]
    user_cfg_paths: list[Path]


@dataclass(frozen=True)
class InstalledLivery:
    package_root: Path
    aircraft_name: str
    name: str
    path: Path
    thumbnail_path: Path | None
    file_count: int
    folder_count: int
    total_size: int
    modified_time: float
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class UninstallReport:
    package_root: Path
    livery_path: Path
    aircraft_name: str
    livery_name: str
    removed_files: int
    removed_dirs: int
    removed_size: int
    layout_entries: int = 0
    manifest_updated: bool = False
    backup_path: Path | None = None
    recovery_path: Path | None = None


@dataclass
class InstallPlan:
    target: Path
    files: list[tuple[Path, Path]]
    liveries: list[str]
    conflicts: list[str]
    warnings: list[str]
    total_size: int
    source_package: Path | None = None
    preview_png: bytes | None = None


@dataclass(frozen=True)
class CommunityCandidate:
    path: Path
    configs: tuple[Path, ...]
    products: tuple[str, ...]
    writable: bool


def normalize_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


def safe_name(value: str, fallback: str = "livery") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned or fallback


def is_relative_to_path(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def is_reparse_point(path: Path) -> bool:
    try:
        attrs = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return bool(attrs & WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT)


def app_resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


def parse_installed_packages_path(user_cfg: Path) -> Path | None:
    try:
        text = user_cfg.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    for line in text.splitlines():
        match = re.match(r'\s*InstalledPackagesPath\s+"?([^"]+)"?\s*$', line)
        if match:
            return normalize_path(match.group(1))
    return None


def detect_msfs2024_paths() -> DetectedPaths:
    local_appdata = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else None
    appdata = Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") else None

    user_cfg_candidates: list[Path] = []
    if local_appdata:
        packages_dir = local_appdata / "Packages"
        if packages_dir.exists():
            for package_dir in packages_dir.glob("Microsoft.Limitless_*"):
                user_cfg_candidates.extend(
                    [
                        package_dir / "LocalState" / "UserCfg.opt",
                        package_dir / "LocalCache" / "UserCfg.opt",
                    ]
                )
    if appdata:
        user_cfg_candidates.append(appdata / "Microsoft Flight Simulator 2024" / "UserCfg.opt")

    user_cfg_paths = [p for p in dict.fromkeys(user_cfg_candidates) if p.exists()]

    community_paths: list[Path] = []
    for cfg_path in user_cfg_paths:
        installed_packages = parse_installed_packages_path(cfg_path)
        if not installed_packages:
            continue
        for folder_name in ("Community", "Community2024"):
            community = installed_packages / folder_name
            if community.exists():
                community_paths.append(community)

    community_paths = list(dict.fromkeys(community_paths))

    return DetectedPaths(
        community_paths=community_paths,
        user_cfg_paths=user_cfg_paths,
    )


def find_pmdg_packages(community_path: Path) -> list[Path]:
    community_path = normalize_path(community_path)
    if not community_path.is_dir():
        return []

    packages: list[Path] = []
    for child in community_path.iterdir():
        checkpoint(f"Scanning product: {child.name}")
        if not child.is_dir():
            continue
        name = child.name.lower()
        if not name.startswith("pmdg-aircraft") or name.endswith("-liveries"):
            continue
        if (child / "layout.json").exists() and (child / "SimObjects").exists():
            packages.append(child)

    return sorted(packages, key=lambda p: p.name.lower())


def base_package_name(package_name: str) -> str:
    package_name = package_name.lower()
    if package_name.endswith("-liveries"):
        return package_name[: -len("-liveries")]
    return package_name


def known_airplane_folder_name(package_root: Path) -> str | None:
    return KNOWN_PMDG_AIRCRAFT_FOLDERS.get(base_package_name(package_root.name))


def find_pmdg_product_roots(community_path: Path) -> list[Path]:
    community_path = normalize_path(community_path)
    product_roots: dict[str, Path] = {}

    for package in find_pmdg_packages(community_path):
        product_roots[package.name.lower()] = package

    if community_path.is_dir():
        for child in community_path.iterdir():
            if not child.is_dir():
                continue
            base_name = base_package_name(child.name)
            if base_name in KNOWN_PMDG_AIRCRAFT_FOLDERS:
                product_roots.setdefault(base_name, community_path / base_name)

    return sorted(product_roots.values(), key=lambda p: p.name.lower())


def validate_package_root(package_root: Path) -> Path:
    package_root = normalize_path(package_root)
    if not package_root.exists() or not package_root.is_dir():
        raise InstallerError(f"PMDG package folder does not exist: {package_root}")
    if not (package_root / "layout.json").exists():
        raise InstallerError(f"layout.json not found in PMDG package: {package_root}")
    if not (package_root / "SimObjects").exists():
        raise InstallerError(f"SimObjects folder not found in PMDG package: {package_root}")
    return package_root


def validate_selected_package_root(package_root: Path) -> Path:
    # Keep the Community alias until linked-target policy has been checked.
    package_root = Path(os.path.abspath(os.path.expandvars(os.path.expanduser(str(package_root)))))
    if package_root.exists():
        if package_root.name.lower().endswith("-liveries") and (package_root / "SimObjects").is_dir():
            return package_root
        validate_package_root(package_root)
        return package_root
    if known_airplane_folder_name(package_root) and package_root.parent.exists():
        return package_root
    raise InstallerError(f"PMDG package folder does not exist: {package_root}")


def ensure_livery_package_root(selected_package_root: Path) -> Path:
    selected_package_root = validate_selected_package_root(selected_package_root)
    if selected_package_root.name.lower().endswith("-liveries"):
        return selected_package_root
    return selected_package_root.parent / f"{selected_package_root.name}-liveries"


def ensure_livery_package_skeleton(livery_package_root: Path, selected_package_root: Path) -> None:
    livery_package_root.mkdir(parents=True, exist_ok=True)
    (livery_package_root / "SimObjects" / "Airplanes").mkdir(parents=True, exist_ok=True)
    layout_path = livery_package_root / "layout.json"
    if not layout_path.exists():
        layout_path.write_text('{"content":[]}\n', encoding="utf-8")
    manifest_path = livery_package_root / "manifest.json"
    if not manifest_path.exists():
        manifest = {
            "dependencies": [],
            "content_type": "AIRCRAFT",
            "title": "Liveries",
            "manufacturer": "PMDG",
            "creator": "PMDG Livery Installer MSFS2024",
            "package_version": "1.0.0",
            "minimum_game_version": "1.20.6",
            "release_notes": {"neutral": {"LastUpdate": "", "OlderHistory": ""}},
            "total_package_size": "0",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def find_livery_package_roots(source_root: Path) -> list[Path]:
    roots: list[Path] = []
    for directory in iter_dirs(source_root):
        name = directory.name.lower()
        if name.startswith("pmdg-aircraft") and name.endswith("-liveries"):
            simobjects = directory / "SimObjects" / "Airplanes"
            if simobjects.exists() and simobjects.is_dir():
                roots.append(directory)
    return sorted(roots, key=lambda p: len(p.parts))


def safe_extract_archive(archive_path: Path, target_dir: Path) -> None:
    archive_path = normalize_path(archive_path)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for item in archive.infolist():
                checkpoint(f"Extracting: {item.filename}")
                raw_name = item.filename.replace("\\", "/")
                rel = PurePosixPath(raw_name)
                if (rel.is_absolute() or ".." in rel.parts or
                        any(":" in part or part.endswith((".", " ")) for part in rel.parts) or
                        ((item.external_attr >> 16) & 0o170000) == 0o120000):
                    raise InstallerError(f"Unsafe path in ZIP: {item.filename}")
                if not rel.name:
                    continue
                destination = target_dir.joinpath(*rel.parts)
                if not is_relative_to_path(destination.resolve(), target_dir.resolve()):
                    raise InstallerError(f"Unsafe path in ZIP: {item.filename}")
                if item.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    raise InstallerError(f"Duplicate file in ZIP: {item.filename}")
                with archive.open(item) as source, destination.open("wb") as dest:
                    while chunk := source.read(1024 * 1024):
                        checkpoint(f"Extracting: {item.filename}")
                        dest.write(chunk)
    except zipfile.BadZipFile as exc:
        raise InstallerError(f"Not a valid ZIP file: {archive_path}") from exc


def contains_installable_content(root: Path) -> bool:
    return bool(find_simobjects_roots(root) or find_direct_livery_folders(root))


def source_root_from_input(input_path: Path, temp_dir: Path) -> Path:
    input_path = normalize_path(input_path)
    if not input_path.exists():
        raise InstallerError(f"Livery path does not exist: {input_path}")

    if input_path.is_file():
        if input_path.suffix.lower() != ".zip":
            raise InstallerError("Only .zip files or extracted livery folders are supported. .ptp import is not supported.")
        extract_root = temp_dir / safe_name(input_path.stem)
        extract_root.mkdir(parents=True, exist_ok=True)
        safe_extract_archive(input_path, extract_root)
        return extract_root

    return input_path


@contextmanager
def temporary_workspace(package_root: Path) -> Iterable[Path]:
    env_tmp = os.environ.get("TEMP") or os.environ.get("TMP")
    candidates: list[Path] = []
    if env_tmp:
        candidates.append(Path(env_tmp))
    candidates.extend([package_root.parent, Path.cwd()])
    last_error: OSError | None = None

    for candidate in candidates:
        tmp_path = candidate / f".pmdg_livery_tmp_{uuid.uuid4().hex}"
        try:
            tmp_path.mkdir(parents=False)
            nested_probe = tmp_path / "probe" / "child"
            nested_probe.mkdir(parents=True)
            probe = nested_probe / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            last_error = exc
            shutil.rmtree(tmp_path, ignore_errors=True)
            continue
        # Do not catch an OSError raised by the caller at yield and retry the
        # operation in another temporary directory.
        try:
            yield tmp_path
            return
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    raise InstallerError(f"Cannot create a writable temporary folder: {last_error}")


def iter_dirs(root: Path) -> Iterable[Path]:
    for current, dirnames, _ in os.walk(root):
        checkpoint(f"Inspecting: {Path(current).name}")
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        yield Path(current)


def find_simobjects_roots(source_root: Path) -> list[Path]:
    roots: list[Path] = []
    for directory in iter_dirs(source_root):
        simobjects = directory / "SimObjects" / "Airplanes"
        if simobjects.exists() and simobjects.is_dir():
            roots.append(directory)
    return sorted(roots, key=lambda p: len(p.parts))


def looks_like_livery_folder(path: Path) -> bool:
    if not path.is_dir():
        return False
    if (path / "livery.cfg").exists():
        return True
    child_names = {child.name.lower() for child in path.iterdir() if child.is_dir()}
    return any(name.startswith(("texture", "model", "panel")) for name in child_names)


def find_direct_livery_folders(source_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for directory in iter_dirs(source_root):
        if looks_like_livery_folder(directory):
            candidates.append(directory)

    filtered: list[Path] = []
    for candidate in sorted(candidates, key=lambda p: len(p.parts)):
        if any(parent in filtered for parent in candidate.parents):
            continue
        filtered.append(candidate)
    return filtered


def count_folder_contents(root: Path) -> tuple[int, int, int]:
    file_count = 0
    folder_count = 0
    total_size = 0
    for current, dirnames, filenames in os.walk(root):
        checkpoint(f"Scanning livery files: {Path(current).name}")
        folder_count += len(dirnames)
        current_path = Path(current)
        for filename in filenames:
            file_count += 1
            try:
                total_size += (current_path / filename).stat().st_size
            except OSError:
                continue
    return file_count, folder_count, total_size


def read_livery_metadata(livery_root: Path) -> dict[str, str]:
    cfg_path = livery_root / "livery.cfg"
    if not cfg_path.exists():
        return {}

    metadata: dict[str, str] = {}
    try:
        lines = cfg_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    except OSError:
        return metadata

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";", "[")):
            continue
        key, separator, value = stripped.partition("=")
        if not separator:
            continue
        key = key.strip().lower()
        value = value.strip().strip('"')
        if key and value:
            metadata.setdefault(key, value)
    return metadata


def find_livery_thumbnail(livery_root: Path) -> Path | None:
    candidates: list[Path] = []
    for current, dirnames, filenames in os.walk(livery_root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        current_path = Path(current)
        for filename in filenames:
            path = current_path / filename
            if path.suffix.lower() not in THUMBNAIL_EXTENSIONS:
                continue
            if path.stem.lower() in THUMBNAIL_STEMS or path.stem.lower().startswith("thumbnail"):
                candidates.append(path)

    if not candidates:
        return None

    def thumbnail_priority(path: Path) -> tuple[int, int, int, str]:
        rel = path.relative_to(livery_root)
        stem = path.stem.lower()
        exact_weight = 0 if stem == "thumbnail" else 1
        suffix_weight = 0 if path.suffix.lower() in {".png", ".jpg", ".jpeg"} else 1
        return (len(rel.parts), exact_weight, suffix_weight, rel.as_posix().lower())

    return sorted(candidates, key=thumbnail_priority)[0]


def livery_parent_roots(livery_package_root: Path) -> list[Path]:
    airplanes = livery_package_root / "SimObjects" / "Airplanes"
    if not airplanes.exists():
        return []
    roots: list[Path] = []
    for aircraft in airplanes.iterdir():
        if not aircraft.is_dir():
            continue
        roots.append(aircraft / "liveries" / "pmdg")
    return roots


def list_installed_liveries(package_root: Path) -> list[InstalledLivery]:
    selected_package_root = validate_selected_package_root(package_root)
    livery_package_root = ensure_livery_package_root(selected_package_root)
    if not livery_package_root.exists():
        return []
    plain_tree(livery_package_root.resolve())

    liveries: list[InstalledLivery] = []
    for livery_parent in livery_parent_roots(livery_package_root):
        if not livery_parent.exists():
            continue
        aircraft_name = livery_parent.parent.parent.name
        for livery_root in livery_parent.iterdir():
            if not livery_root.is_dir() or not looks_like_livery_folder(livery_root):
                continue
            file_count, folder_count, total_size = count_folder_contents(livery_root)
            try:
                modified_time = livery_root.stat().st_mtime
            except OSError:
                modified_time = 0.0
            liveries.append(
                InstalledLivery(
                    package_root=livery_package_root,
                    aircraft_name=aircraft_name,
                    name=livery_root.name,
                    path=livery_root,
                    thumbnail_path=find_livery_thumbnail(livery_root),
                    file_count=file_count,
                    folder_count=folder_count,
                    total_size=total_size,
                    modified_time=modified_time,
                    metadata=read_livery_metadata(livery_root),
                )
            )

    return sorted(liveries, key=lambda item: (item.aircraft_name.lower(), item.name.lower()))


def resolve_installed_livery(package_root: Path, identifier: str | Path) -> InstalledLivery:
    selected_package_root = validate_selected_package_root(package_root)
    raw_identifier = str(identifier).strip()
    if not raw_identifier:
        raise InstallerError("Select an installed livery to uninstall.")

    liveries = list_installed_liveries(selected_package_root)
    identifier_path = Path(raw_identifier)
    if identifier_path.is_absolute() or identifier_path.exists() or any(sep in raw_identifier for sep in ("/", "\\")):
        try:
            normalized_identifier = normalize_path(identifier_path)
        except OSError:
            normalized_identifier = identifier_path
        for livery in liveries:
            if normalize_path(livery.path) == normalized_identifier:
                return livery

    normalized_name = raw_identifier.casefold()
    matches = [
        livery
        for livery in liveries
        if livery.name.casefold() == normalized_name
        or f"{livery.aircraft_name}/{livery.name}".casefold() == normalized_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise InstallerError(
            "More than one installed livery matches that name. "
            "Use Aircraft/Livery Name or the full livery folder path."
        )
    raise InstallerError(f"Installed livery not found: {raw_identifier}")


def uninstall_livery(
    package_root: Path,
    livery_identifier: str | Path,
    backup_layout: bool = True,
    allow_linked_targets: bool = False,
) -> UninstallReport:
    return uninstall_liveries(package_root, [livery_identifier], backup_layout, allow_linked_targets)[0]


def uninstall_liveries(package_root: Path, identifiers: list[str | Path],
                       backup_layout: bool = True, allow_linked_targets: bool = False) -> list[UninstallReport]:
    selected_package_root = validate_selected_package_root(package_root)
    livery_package_root = checked_livery_target(selected_package_root, allow_linked_targets)
    if not livery_package_root.exists():
        raise InstallerError(f"Livery package does not exist: {livery_package_root}")
    if is_reparse_point(livery_package_root) and not allow_linked_targets:
        raise InstallerError(
            "The target livery package is a symlink/junction/reparse-point folder. "
            "Uninstall is blocked by default to avoid deleting files from a linked source folder."
        )

    liveries = {str(livery.path.resolve()): livery for identifier in identifiers
                for livery in [resolve_installed_livery(selected_package_root, identifier)]}
    if not liveries:
        raise InstallerError("Select at least one installed livery.")
    with PackageTransaction(livery_package_root, "uninstall") as transaction:
        for livery in liveries.values():
            relative = livery.path.resolve().relative_to(livery_package_root)
            staged_livery = transaction.stage / relative
            if not is_relative_to_path(staged_livery.resolve(), transaction.stage.resolve()) or len(relative.parts) != 6:
                raise InstallerError("The selected folder is not a single installed livery.")
            checkpoint(f"Removing from staged package: {livery.name}", force=True)
            shutil.rmtree(staged_livery)
        layout_entries, manifest_updated, backup_path = rebuild_layout(transaction.stage, backup=backup_layout)
        recovery = transaction.commit()
    if backup_path:
        backup_path = livery_package_root / backup_path.name
    return [UninstallReport(
        package_root=livery_package_root, livery_path=livery.path,
        aircraft_name=livery.aircraft_name, livery_name=livery.name,
        removed_files=livery.file_count, removed_dirs=livery.folder_count,
        removed_size=livery.total_size, layout_entries=layout_entries,
        manifest_updated=manifest_updated, backup_path=backup_path, recovery_path=recovery,
    ) for livery in liveries.values()]


def get_single_airplane_folder(package_root: Path) -> Path:
    airplanes = package_root / "SimObjects" / "Airplanes"
    if not airplanes.exists():
        raise InstallerError(f"Airplanes folder not found: {airplanes}")

    airplane_folders = [p for p in airplanes.iterdir() if p.is_dir()]
    if len(airplane_folders) == 1:
        return airplane_folders[0]

    pmdg_folders = [p for p in airplane_folders if p.name.lower().startswith("pmdg")]
    if len(pmdg_folders) == 1:
        return pmdg_folders[0]

    names = ", ".join(p.name for p in airplane_folders)
    raise InstallerError(
        "Cannot choose aircraft folder automatically. "
        f"Found {len(airplane_folders)} folders: {names}"
    )


def get_airplane_folder_name(selected_package_root: Path, livery_package_root: Path) -> str:
    if selected_package_root.exists() and not selected_package_root.name.lower().endswith("-liveries"):
        # Actual installed aircraft/variant folders take precedence over the
        # generic fallback used for virtual (Marketplace/streamed) products.
        return get_single_airplane_folder(selected_package_root).name
    known_folder = known_airplane_folder_name(selected_package_root)
    if known_folder:
        return known_folder
    livery_airplanes = livery_package_root / "SimObjects" / "Airplanes"
    if livery_airplanes.exists():
        livery_folders = [p for p in livery_airplanes.iterdir() if p.is_dir()]
        if len(livery_folders) == 1:
            return livery_folders[0].name

    selected_airplane = get_single_airplane_folder(selected_package_root)
    return selected_airplane.name


def should_skip_root_item(path: Path) -> bool:
    lower_name = path.name.lower()
    if lower_name in ROOT_EXCLUDE_NAMES:
        return True
    if lower_name.startswith("layout.json.bak-"):
        return True
    return False


def copy_path(src: Path, dest: Path, overwrite: bool) -> tuple[int, int]:
    copied_files = 0
    copied_dirs = 0

    if src.is_dir():
        if dest.exists() and not dest.is_dir():
            raise InstallerError(f"Destination exists and is not a folder: {dest}")
        dest.mkdir(parents=True, exist_ok=True)
        copied_dirs += 1
        for current, dirnames, filenames in os.walk(src):
            current_path = Path(current)
            rel = current_path.relative_to(src)
            target_dir = dest / rel
            target_dir.mkdir(parents=True, exist_ok=True)
            for dirname in dirnames:
                (target_dir / dirname).mkdir(exist_ok=True)
                copied_dirs += 1
            for filename in filenames:
                source_file = current_path / filename
                target_file = target_dir / filename
                if target_file.exists() and not overwrite:
                    raise InstallerError(f"Destination file already exists: {target_file}")
                shutil.copy2(source_file, target_file)
                copied_files += 1
        return copied_files, copied_dirs

    if dest.exists() and not overwrite:
        raise InstallerError(f"Destination file already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return 1, 0


def copy_package_contents(source_root: Path, package_root: Path, overwrite: bool) -> tuple[int, int, list[Path]]:
    copied_files = 0
    copied_dirs = 0
    installed_roots: list[Path] = []

    for item in source_root.iterdir():
        if should_skip_root_item(item):
            continue
        dest = package_root / item.name
        files, dirs = copy_path(item, dest, overwrite=overwrite)
        copied_files += files
        copied_dirs += dirs
        installed_roots.append(dest)

    return copied_files, copied_dirs, installed_roots


def copy_livery_package_contents(
    source_package_root: Path,
    livery_package_root: Path,
    overwrite: bool,
) -> tuple[int, int, list[Path]]:
    copied_files = 0
    copied_dirs = 0
    installed_roots: list[Path] = []

    for item in source_package_root.iterdir():
        lower_name = item.name.lower()
        if lower_name in {"layout.json", "msfslayoutgenerator.exe"} or lower_name.startswith("layout.json.bak-"):
            continue
        if lower_name == "manifest.json" and (livery_package_root / "manifest.json").exists() and not overwrite:
            continue
        dest = livery_package_root / item.name
        files, dirs = copy_path(item, dest, overwrite=overwrite)
        copied_files += files
        copied_dirs += dirs
        installed_roots.append(dest)

    return copied_files, copied_dirs, installed_roots


def copy_direct_liveries(
    livery_folders: list[Path],
    selected_package_root: Path,
    livery_package_root: Path,
    overwrite: bool,
) -> tuple[int, int, list[Path]]:
    airplane_folder_name = get_airplane_folder_name(selected_package_root, livery_package_root)
    livery_target = (
        livery_package_root
        / "SimObjects"
        / "Airplanes"
        / airplane_folder_name
        / "liveries"
        / "pmdg"
    )
    livery_target.mkdir(parents=True, exist_ok=True)

    copied_files = 0
    copied_dirs = 0
    installed_roots: list[Path] = []

    for livery_folder in livery_folders:
        dest = livery_target / livery_folder.name
        files, dirs = copy_path(livery_folder, dest, overwrite=overwrite)
        copied_files += files
        copied_dirs += dirs
        installed_roots.append(dest)

    return copied_files, copied_dirs, installed_roots


def windows_filetime(path: Path) -> int:
    return int((path.stat().st_mtime + WINDOWS_FILETIME_EPOCH_OFFSET) * 10_000_000)


def iter_layout_files(package_root: Path) -> Iterable[Path]:
    for current, dirnames, filenames in os.walk(package_root):
        checkpoint(f"Checking layout files: {Path(current).name}")
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        current_path = Path(current)
        for filename in filenames:
            file_path = current_path / filename
            rel = file_path.relative_to(package_root)
            if len(rel.parts) == 1 and should_skip_root_item(file_path):
                continue
            if rel.name.lower().endswith(".tmp"):
                continue
            yield file_path


def build_layout_content(package_root: Path) -> list[dict[str, int | str]]:
    entries = []
    for file_path in iter_layout_files(package_root):
        rel = file_path.relative_to(package_root).as_posix()
        entries.append(
            {
                "path": rel,
                "size": file_path.stat().st_size,
                "date": windows_filetime(file_path),
            }
        )
    return sorted(entries, key=lambda item: str(item["path"]).lower())


def update_manifest_size(package_root: Path, total_size: int) -> bool:
    manifest_path = package_root / "manifest.json"
    if not manifest_path.exists():
        return False

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False

    if not isinstance(data, dict) or "total_package_size" not in data:
        return False

    data["total_package_size"] = str(total_size)
    manifest_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def rebuild_layout(package_root: Path, backup: bool = True) -> tuple[int, bool, Path | None]:
    package_root = validate_package_root(package_root)
    layout_path = package_root / "layout.json"
    backup_path: Path | None = None

    original_layout = layout_path.read_bytes()
    checkpoint("Rebuilding livery package index", force=True)
    generator_path = layout_generator_path()
    try:
        process = subprocess.Popen(
            [str(generator_path), str(layout_path)],
            cwd=str(package_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        try:
            deadline = time.monotonic() + 120
            while True:
                checkpoint("Rebuilding livery package index (up to 120 seconds)")
                try:
                    stdout, stderr = process.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise InstallerError("Layout generator timed out after 120 seconds. The active package was not changed.")
            if process.returncode:
                raise InstallerError(f"MSFSLayoutGenerator failed ({process.returncode}): {(stderr or stdout).strip()}")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        entry_count, total_size = validate_layout(package_root)
        generated = json.loads(layout_path.read_text(encoding="utf-8-sig"))
        generated["content"] = [entry for entry in generated["content"]
                                if not (len(PurePosixPath(entry["path"].replace("\\", "/")).parts) == 1
                                        and should_skip_root_item(Path(entry["path"])))]
        write_json(layout_path, generated)
        manifest_updated = update_manifest_size(package_root, total_size)
        if backup:
            timestamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            backup_path = package_root / f"layout.json.bak-{timestamp}"
            backup_path.write_bytes(original_layout)
        return entry_count, manifest_updated, backup_path
    except BaseException:
        layout_path.write_bytes(original_layout)
        raise


def validate_layout(package_root: Path) -> tuple[int, int]:
    checkpoint("Validating generated index against package files", force=True)
    try:
        layout = json.loads((package_root / "layout.json").read_text(encoding="utf-8-sig"))
        manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallerError(f"Package JSON is missing or unreadable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise InstallerError("manifest.json must contain a JSON object.")
    if not isinstance(layout, dict) or not isinstance(layout.get("content"), list):
        raise InstallerError("layout.json must contain a content array.")
    actual = {}
    for entry in layout["content"]:
        checkpoint()
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                or type(entry.get("size")) is not int or entry["size"] < 0
                or type(entry.get("date")) is not int or entry["date"] < 0):
            raise InstallerError("layout.json contains an invalid file entry.")
        name = entry["path"].replace("\\", "/")
        rel = PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts or ":" in name or not rel.parts:
            raise InstallerError(f"layout.json contains an unsafe path: {name}")
        key = rel.as_posix().casefold()
        if key in actual:
            raise InstallerError(f"layout.json contains a duplicate entry: {name}")
        # The bundled generator can list old layout backups; they are metadata,
        # not livery content, and are excluded from the saved index below.
        if len(rel.parts) == 1 and should_skip_root_item(Path(name)):
            continue
        actual[key] = entry["size"]
    expected = {str(entry["path"]).casefold(): entry["size"] for entry in build_layout_content(package_root)}
    missing = sorted(expected.keys() - actual.keys())
    stale = sorted(actual.keys() - expected.keys())
    changed = sorted(key for key in expected.keys() & actual.keys() if expected[key] != actual[key])
    if missing or stale or changed:
        parts = []
        for label, paths in (("Missing index entries", missing), ("Files missing from disk", stale), ("File size differs (modified, not necessarily damaged)", changed)):
            if paths:
                parts.append(f"{label}: {len(paths)}; {paths[0]}")
        raise InstallerError("; ".join(parts) + ". Review changes, then rebuild the livery layout.")
    return len(actual), sum(actual.values())


def rebuild_livery_layout(package_root: Path, backup: bool = True, allow_linked_targets: bool = False):
    target = checked_livery_target(package_root, allow_linked_targets)
    if not target.is_dir():
        raise InstallerError(f"No companion livery package exists yet: {target}. Install a livery first.")
    with PackageTransaction(target, "rebuild layout") as transaction:
        # A missing index is repairable; a missing manifest is not invented here.
        if not (transaction.stage / "layout.json").exists():
            write_json(transaction.stage / "layout.json", {"content": []})
        result = rebuild_layout(transaction.stage, backup)
        recovery = transaction.commit()
    return target, result[0], result[1], recovery


def restore_package_backup(package_root: Path, backup: Path, allow_linked_targets: bool = False):
    target = checked_livery_target(package_root, allow_linked_targets)
    backup = normalize_path(backup)
    plain_tree(backup)
    try:
        record = json.loads((backup / "recovery.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallerError("Choose a backup folder containing recovery.json and package/.") from exc
    if not isinstance(record, dict) or record.get("format") != 1 or normalize_path(record.get("target", "")) != target:
        raise InstallerError("This backup belongs to a different package/location. Select its original aircraft and Community folder.")
    source = backup / "package"
    if not source.is_dir() or not (source / "SimObjects").is_dir():
        raise InstallerError("The backup has no recoverable package files.")
    with PackageTransaction(target, "restore backup") as transaction:
        shutil.rmtree(transaction.stage)
        copy_tree(source, transaction.stage)
        if not (transaction.stage / "layout.json").exists():
            write_json(transaction.stage / "layout.json", {"content": []})
        rebuild_layout(transaction.stage)
        recovery = transaction.commit()
    return f"Restored: {target}\nBackup used: {backup}\nPrevious state backup: {recovery or 'No previous package'}"


def export_liveries(package_root: Path, identifiers: list[str | Path], destination: Path) -> Path:
    target = ensure_livery_package_root(package_root).resolve()
    destination = Path(os.path.abspath(destination))
    if is_relative_to_path(destination.resolve(), target):
        raise InstallerError("Save the export outside the livery package.")
    liveries = [resolve_installed_livery(package_root, identifier) for identifier in identifiers]
    if not liveries:
        raise InstallerError("Select at least one livery to export.")
    temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for livery in liveries:
                plain_tree(livery.path)
                for path in livery.path.rglob("*"):
                    if path.is_file():
                        checkpoint(f"Exporting: {livery.name}/{path.name}")
                        relative = path.resolve().relative_to(target)
                        with path.open("rb") as source, archive.open(relative.as_posix(), "w", force_zip64=True) as output:
                            while block := source.read(1024 * 1024):
                                checkpoint(f"Exporting: {path.name}")
                                output.write(block)
        checkpoint("Saving livery export", force=True)
        temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def community_candidates() -> list[CommunityCandidate]:
    detected = detect_msfs2024_paths()
    candidates = []
    for community in detected.community_paths:
        checkpoint(f"Inspecting Community: {community}")
        configs = tuple(cfg for cfg in detected.user_cfg_paths
                        if parse_installed_packages_path(cfg) == community.parent.resolve())
        products = tuple(path.name for path in find_pmdg_product_roots(community))
        candidates.append(CommunityCandidate(community, configs, products, os.access(community, os.W_OK)))
    return candidates


def filter_liveries(liveries: list[InstalledLivery], query: str = "", thumbnail_filter: str = "All") -> list[InstalledLivery]:
    words = query.casefold().split()
    return [livery for livery in liveries
            if all(word in " ".join([livery.name, livery.aircraft_name, *livery.metadata.values()]).casefold() for word in words)
            and (thumbnail_filter != "Missing thumbnail" or livery.thumbnail_path is None)]


def diagnose_package(package_root: Path, allow_linked_targets: bool = False) -> str:
    lines = ["PMDG Livery Installer MSFS2024 Diagnostics", f"Selected aircraft: {package_root}"]
    try:
        target = ensure_livery_package_root(package_root)
        lines.append(f"Companion livery package: {target}")
        real = checked_livery_target(package_root, allow_linked_targets)
        if real != target:
            lines.append(f"Real location: {real}")
        lines.append("PASS: Target path/link policy checked.")
        if not real.exists():
            lines.append("UNKNOWN: No livery package exists. Install a compatible livery before repairing.")
            return "\n".join(lines)
        lines.append("PASS: Folder permits writing (OS estimate)." if os.access(real, os.W_OK) else "FAIL: Folder is not writable. Choose the correct Community folder or fix its permissions.")
        try:
            entries, size = validate_layout(real)
            lines.append(f"PASS: Index matches {entries} files ({format_bytes(size)}).")
        except InstallerError as exc:
            lines.append(f"FAIL: {exc}")
        liveries = list_installed_liveries(package_root)
        lines.append(f"PASS: {len(liveries)} livery folders recognized in the companion package.")
        missing = sum(livery.thumbnail_path is None for livery in liveries)
        if missing:
            lines.append(f"INFO: {missing} liveries have no supported thumbnail. This does not establish that the livery is damaged.")
        snapshots = backup_directory(real)
        count = len(list(snapshots.glob("*/recovery.json"))) if snapshots.exists() else 0
        lines.append(f"Recovery backups: {count}; {snapshots}")
        lines.append("UNKNOWN: Simulator loading and winglet/engine compatibility require checking the download description and MSFS 2024.")
        lines.append("Action: Rebuild livery layout repairs the index only. It does not replace modified texture/config files. Restore backup replaces the whole companion package.")
    except (InstallerError, OSError) as exc:
        lines.append(f"FAIL: {exc}")
    return "\n".join(lines)


def redact_report(text: str, paths: list[Path]) -> str:
    for index, path in enumerate(sorted({str(p) for p in paths if str(p)}, key=len, reverse=True), 1):
        text = re.sub(re.escape(path), lambda _match, i=index: f"<PATH_{i}>", text, flags=re.I)
        text = re.sub(re.escape(path.replace("\\", "/")), lambda _match, i=index: f"<PATH_{i}>", text, flags=re.I)
    return text


def layout_generator_path() -> Path:
    override = os.environ.get("PMDG_LAYOUT_GENERATOR")
    if override:
        path = normalize_path(override)
    else:
        path = app_resource_path("assets/MSFSLayoutGenerator.exe")
    if not path.exists():
        raise InstallerError("Bundled MSFSLayoutGenerator.exe was not found.")
    return path


def validate_install_safety(
    livery_input: Path,
    source_root: Path,
    selected_package_root: Path,
    livery_package_root: Path,
    allow_linked_targets: bool,
) -> None:
    input_root = normalize_path(livery_input)
    if input_root.is_file():
        input_root = input_root.parent
    source_root = normalize_path(source_root)
    selected_package_root = normalize_path(selected_package_root)
    existing_livery_target = livery_package_root if livery_package_root.exists() else None
    if existing_livery_target and is_reparse_point(existing_livery_target) and not allow_linked_targets:
        raise InstallerError(
            "The target livery package is a symlink/junction/reparse-point folder. "
            "This is common with MSFS Addons Linker and is blocked by default to avoid "
            "writing into a linked source folder. Move or create a real Community "
            "livery package, or enable linked target installs only if you intentionally "
            "want to modify the linked target."
        )

    overlap_targets = [livery_package_root]
    if selected_package_root.exists():
        overlap_targets.append(selected_package_root)

    for target in overlap_targets:
        if not target.exists():
            continue
        target_resolved = normalize_path(target)
        if is_relative_to_path(input_root, target_resolved) or is_relative_to_path(source_root, target_resolved):
            raise InstallerError(
                "The selected livery source is inside the target PMDG package. "
                "Choose a source outside Community to avoid copying a package into itself."
            )
        if is_relative_to_path(target_resolved, source_root):
            raise InstallerError(
                "The target PMDG package is inside the selected livery source. "
                "Choose a narrower source folder or a different target package."
            )


def checked_livery_target(package_root: Path, allow_linked_targets: bool = False) -> Path:
    target = ensure_livery_package_root(package_root)
    for path in (target, *target.parents):
        if path.exists() and is_reparse_point(path) and not allow_linked_targets:
            raise InstallerError(
                f"Target uses a symlink/junction: {path}. Enable Allow linked targets only "
                "if you intend to modify its real location."
            )
    resolved = target.resolve()
    # Opt-in permits a linked package root, never nested links in the package.
    plain_tree(resolved)
    return resolved


def prepare_install_plan(source: Path, selected: Path, target: Path, overwrite: bool) -> InstallPlan:
    checkpoint("Inspecting livery structure and compatibility", force=True)
    plain_tree(source)
    packages = find_livery_package_roots(source)
    simobjects = find_simobjects_roots(source)
    files: list[tuple[Path, Path]] = []
    warnings = []
    liveries = []
    source_package = None
    expected_name = ensure_livery_package_root(selected).name.lower()
    if len(packages) > 1 or (not packages and len(simobjects) > 1):
        raise InstallerError("Multiple aircraft packages found. Select one extracted package folder at a time; none was installed.")
    if packages or simobjects:
        content_root = (packages or simobjects)[0]
        if packages:
            source_package = content_root
            if content_root.name.lower() != expected_name:
                raise InstallerError(f"Aircraft mismatch: source {content_root.name}, target {expected_name}.")
        aircraft_root = content_root / "SimObjects" / "Airplanes"
        known = known_airplane_folder_name(selected)
        installed_airplanes = selected / "SimObjects" / "Airplanes"
        allowed = {p.name.casefold() for p in installed_airplanes.iterdir() if p.is_dir()} if installed_airplanes.exists() else set()
        if known:
            allowed.add(known.casefold())
        for aircraft in aircraft_root.iterdir():
            if not aircraft.is_dir():
                continue
            if aircraft.name.casefold() not in allowed:
                raise InstallerError(f"Aircraft/variant mismatch: {aircraft.name}. Select the matching aircraft package; expected {', '.join(sorted(allowed))}.")
            parent = aircraft / "liveries" / "pmdg"
            found = [p for p in parent.iterdir() if p.is_dir() and looks_like_livery_folder(p)] if parent.is_dir() else []
            if not found:
                raise InstallerError(f"Unsupported aircraft structure in {aircraft.name}. Expected MSFS 2024 liveries/pmdg/<livery>. MSFS 2020 aircraft.cfg packages are not converted.")
            liveries.extend(f"{aircraft.name}/{p.name}" for p in found)
        for path in content_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(content_root)
            if should_skip_root_item(Path(rel.parts[0])):
                if not (packages and rel.as_posix().lower() == "manifest.json" and (overwrite or not (target / "manifest.json").exists())):
                    continue
            files.append((path, rel))
    else:
        direct = find_direct_livery_folders(source)
        if not direct:
            ptp = next(source.rglob("*.ptp"), None)
            if ptp:
                raise InstallerError("This ZIP contains PTP files. PTP import is unsupported; download an MSFS 2024 ZIP or extracted livery folder.")
            raise InstallerError("No installable livery found. Expected an MSFS 2024 PMDG livery.cfg or compatible texture folder.")
        aircraft = get_airplane_folder_name(selected, target)
        for folder in direct:
            if (folder / "aircraft.cfg").exists() and not (folder / "livery.cfg").exists():
                raise InstallerError("This appears to be a legacy aircraft.cfg livery. MSFS 2020 conversion is not supported.")
            relroot = Path("SimObjects") / "Airplanes" / aircraft / "liveries" / "pmdg" / folder.name
            liveries.append(f"{aircraft}/{folder.name}")
            for path in folder.rglob("*"):
                if path.is_file():
                    files.append((path, relroot / path.relative_to(folder)))
        warnings.append("Direct folders do not reliably identify simulator version or winglet/engine variant. Confirm that the download is for this MSFS 2024 aircraft.")
    if not liveries or not files:
        raise InstallerError("No livery files were found in the selected package.")
    # Explicit aircraft references are evidence; absence is not compatibility.
    expected_aircraft = known_airplane_folder_name(selected)
    if expected_aircraft:
        for path, relative in files:
            if path.name.lower() not in {"livery.cfg", "aircraft.cfg"}:
                continue
            aircraft = relative.parts[2] if len(relative.parts) >= 3 else expected_aircraft
            expected_model = re.search(r"(?:737|777)-\d+[A-Z]*", aircraft, re.I)
            text = "\n".join(line for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
                             if line.strip().lower().startswith("base_container"))
            for model in re.findall(r"PMDG[ _-]*((?:737|777)-\d+[A-Z]*)", text, re.I):
                if expected_model and model.upper() != expected_model.group().upper():
                    family = re.match(r"(?:737|777)-\d+", model, re.I).group().upper()
                    expected_family = re.match(r"(?:737|777)-\d+", expected_model.group(), re.I).group().upper()
                    source_variant = model.upper()[len(family):]
                    target_variant = expected_model.group().upper()[len(expected_family):]
                    if family != expected_family or (source_variant and target_variant and source_variant != target_variant):
                        raise InstallerError(f"Aircraft reference mismatch in {path.name}: {model}; selected {aircraft}.")
                    warnings.append(f"Variant reference {model} found. Confirm that this variant is installed; generic aircraft names cannot establish variant compatibility.")
    seen = set()
    conflicts = []
    total = 0
    for path, rel in files:
        checkpoint(f"Checking destination: {rel.name}")
        key = rel.as_posix().casefold()
        if key in seen:
            raise InstallerError(f"Duplicate destination in source: {rel}")
        seen.add(key)
        destination = target / rel
        if destination.exists():
            if not destination.is_file():
                raise InstallerError(f"Destination is a folder, but source is a file: {destination}")
            conflicts.append(rel.as_posix())
        for parent in destination.parents:
            if parent == target:
                break
            if parent.exists() and not parent.is_dir():
                raise InstallerError(f"A destination parent is not a folder: {parent}")
        total += path.stat().st_size
    warnings.append("File checks cannot confirm in-simulator loading. Check this aircraft's livery list in MSFS 2024 after installation.")
    return InstallPlan(target, files, sorted(liveries), conflicts, warnings, total, source_package)


def preview_install(livery_input: Path, package_root: Path, overwrite: bool = False,
                    allow_linked_targets: bool = False) -> InstallPlan:
    selected = validate_selected_package_root(package_root)
    target = checked_livery_target(selected, allow_linked_targets)
    with temporary_workspace(target) as tmp:
        source = source_root_from_input(livery_input, tmp)
        validate_install_safety(livery_input, source, selected, target, allow_linked_targets)
        plan = prepare_install_plan(source, selected, target, overwrite)
        thumbnails = [path for path, _ in plan.files if path.suffix.lower() in THUMBNAIL_EXTENSIONS
                      and path.stem.lower().startswith("thumbnail")]
        if thumbnails:
            try:
                from PIL import Image
                with Image.open(thumbnails[0]) as image:
                    image.thumbnail((480, 150))
                    output = io.BytesIO()
                    image.convert("RGBA").save(output, format="PNG")
                    plan.preview_png = output.getvalue()
            except (ImportError, OSError, ValueError):
                plan.warnings.append("The source thumbnail could not be previewed; livery file checks still apply.")
        return plan


def format_install_plan(plan: InstallPlan) -> str:
    lines = [f"Target: {plan.target}", f"Liveries: {len(plan.liveries)}", *[f"  {name}" for name in plan.liveries],
             f"Files: {len(plan.files)} ({format_bytes(plan.total_size)})", f"Existing files to replace: {len(plan.conflicts)}"]
    lines.extend(f"  {path}" for path in plan.conflicts[:20])
    if len(plan.conflicts) > 20:
        lines.append(f"  ... and {len(plan.conflicts) - 20} more")
    lines.extend(f"Note: {warning}" for warning in plan.warnings)
    return "\n".join(lines)


def install_livery(
    livery_input: Path,
    package_root: Path,
    overwrite: bool = False,
    backup_layout: bool = True,
    allow_linked_targets: bool = False,
) -> InstallReport:
    selected_package_root = validate_selected_package_root(package_root)
    livery_package_root = checked_livery_target(selected_package_root, allow_linked_targets)

    with temporary_workspace(livery_package_root) as tmp:
        source_root = source_root_from_input(livery_input, tmp)
        validate_install_safety(
            livery_input,
            source_root,
            selected_package_root,
            livery_package_root,
            allow_linked_targets=allow_linked_targets,
        )

        plan = prepare_install_plan(source_root, selected_package_root, livery_package_root, overwrite)
        if plan.conflicts and not overwrite:
            raise InstallerError(f"{len(plan.conflicts)} destination file(s) already exist. Review the preflight report and enable Allow overwrite to replace them. First: {plan.conflicts[0]}")
        if shutil.disk_usage(livery_package_root.parent).free < plan.total_size * 2 + sum(p.stat().st_size for p in livery_package_root.rglob("*") if p.is_file()):
            raise InstallerError("Not enough disk space for this install and its recoverable package copy.")
        with PackageTransaction(livery_package_root, "install") as transaction:
            for index, (source, relative) in enumerate(plan.files, 1):
                checkpoint(f"Installing file {index}/{len(plan.files)}: {relative.name}")
                copy_file(source, transaction.stage / relative)
            ensure_livery_package_skeleton(transaction.stage, selected_package_root)
            layout_entries, manifest_updated, backup_path = rebuild_layout(transaction.stage, backup=backup_layout)
            recovery = transaction.commit()
            if backup_path:
                backup_path = livery_package_root / backup_path.name
    installed_roots = [livery_package_root / "SimObjects" / "Airplanes" / name.split("/")[0] / "liveries" / "pmdg" / name.split("/", 1)[1] for name in plan.liveries]
    return InstallReport(
        package_root=livery_package_root,
        source_package_root=normalize_path(livery_input) if plan.source_package else None,
        copied_files=len(plan.files),
        copied_dirs=len({rel.parent for _, rel in plan.files}),
        layout_entries=layout_entries,
        manifest_updated=manifest_updated,
        backup_path=backup_path,
        installed_roots=installed_roots,
        recovery_path=recovery,
        warnings=plan.warnings,
    )


def format_report(report: InstallReport) -> str:
    lines = [
        "File copy and layout validation passed.",
        f"Livery package: {report.package_root}",
        f"Copied files: {report.copied_files}",
        f"Copied folders: {report.copied_dirs}",
        f"layout.json entries: {report.layout_entries}",
        f"manifest.json updated: {'yes' if report.manifest_updated else 'no'}",
    ]
    if report.source_package_root:
        lines.append(f"Source package: {report.source_package_root}")
    if report.backup_path:
        lines.append(f"layout backup: {report.backup_path}")
    if report.recovery_path:
        lines.append(f"Full recovery backup: {report.recovery_path}")
    lines.extend(f"Note: {warning}" for warning in report.warnings)
    if report.installed_roots:
        lines.append("Installed roots:")
        lines.extend(f"  - {path}" for path in report.installed_roots)
    return "\n".join(lines)


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def format_uninstall_report(report: UninstallReport) -> str:
    lines = [
        f"Livery package: {report.package_root}",
        f"Removed livery: {report.aircraft_name}/{report.livery_name}",
        f"Removed folder: {report.livery_path}",
        f"Removed files: {report.removed_files}",
        f"Removed folders: {report.removed_dirs}",
        f"Removed size: {format_bytes(report.removed_size)}",
        f"layout.json entries: {report.layout_entries}",
        f"manifest.json updated: {'yes' if report.manifest_updated else 'no'}",
    ]
    if report.backup_path:
        lines.append(f"layout backup: {report.backup_path}")
    if report.recovery_path:
        lines.append(f"Full recovery backup: {report.recovery_path}")
    return "\n".join(lines)


def launch_gui(run_mainloop: bool = True, detect_on_start: bool = True):
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from livery_ui import COLORS as UI_COLORS, build_interface
    from app_version import VERSION

    class InstallerApp(tk.Tk):
        COLORS = UI_COLORS

        asset_path = staticmethod(app_resource_path)
        format_size = staticmethod(format_bytes)

        def __init__(self) -> None:
            super().__init__()
            self.title(f"PMDG Livery Installer MSFS2024 · v{VERSION}")
            self.geometry("1360x860")
            self.minsize(1180, 780)
            self.configure(bg=self.COLORS["bg"])
            icon_path = app_resource_path("assets/pmdg_livery_installer_icon.ico")
            if icon_path.exists():
                self.iconbitmap(str(icon_path))

            self.community_var = tk.StringVar()
            self.livery_var = tk.StringVar()
            self.package_var = tk.StringVar()
            self.status_var = tk.StringVar(value="Ready")
            self.package_count_var = tk.StringVar(value="0 products detected")
            self.overwrite_var = tk.BooleanVar(value=False)
            self.backup_var = tk.BooleanVar(value=True)
            self.allow_linked_targets_var = tk.BooleanVar(value=False)
            self.package_paths: dict[str, Path] = {}
            self.detected_packages: list[Path] = []
            self.installed_liveries: list[InstalledLivery] = []
            self.installed_livery_items: dict[str, InstalledLivery] = {}
            self.thumbnail_image = None
            self.nav_buttons: dict[str, tk.Button] = {}
            self.pages: dict[str, tk.Frame] = {}
            self.busy = False
            self.job_queue = queue.Queue()
            self.job_control = None
            self.close_when_idle = False
            self.disabled_widgets = []
            self.search_var = tk.StringVar()
            self.thumbnail_filter_var = tk.StringVar(value="All")
            self.hide_paths_var = tk.BooleanVar(value=True)
            self.thumbnail_cache = {}
            self.settings_path = (
                Path(os.environ.get("APPDATA", Path.home()))
                / "PMDG Livery Installer MSFS2024"
                / "settings.json"
            )
            self._load_settings()

            self._build_ui()
            self.protocol("WM_DELETE_WINDOW", self.on_close)
            self.after(80, self.poll_job)
            if detect_on_start:
                self.after(100, self.detect_paths)
            self.show_page("Installed")

        def walk_widgets(self, parent):
            for child in parent.winfo_children():
                yield child
                yield from self.walk_widgets(child)

        def run_job(self, title, work, done):
            if self.busy:
                return
            self.busy = True
            self.job_done = done
            self.job_title = title
            self.job_control = OperationControl(notify=lambda text: self.job_queue.put(("progress", text)))
            self.disabled_widgets = []
            for widget in self.walk_widgets(self):
                if widget in self.nav_buttons.values() or widget is self.cancel_button:
                    continue
                if isinstance(widget, (tk.Button, tk.Entry, tk.Checkbutton, tk.Listbox, ttk.Combobox)):
                    self.disabled_widgets.append((widget, str(widget.cget("state"))))
                    widget.configure(state="disabled")
            self.cancel_button.configure(state="normal")
            self.progress_bar.start(12)
            self.status_var.set(title)
            self.log(title)
            control = self.job_control

            def worker():
                try:
                    with operation_context(control):
                        result = work()
                    self.job_queue.put(("result", result))
                except Exception as exc:
                    self.job_queue.put(("error", exc))

            threading.Thread(target=worker, name="Livery worker", daemon=False).start()

        def poll_job(self):
            try:
                for _ in range(200):
                    kind, payload = self.job_queue.get_nowait()
                    if kind == "thumbnail":
                        self.finish_thumbnail(payload)
                        continue
                    if kind == "progress":
                        self.status_var.set(payload[:110])
                        continue
                    self.busy = False
                    self.progress_bar.stop()
                    self.cancel_button.configure(state="disabled")
                    for widget, state in self.disabled_widgets:
                        if widget.winfo_exists():
                            widget.configure(state=state)
                    self.disabled_widgets = []
                    if kind == "error":
                        self.log(f"{self.job_title}: {payload}")
                        self.status_var.set("Cancelled" if isinstance(payload, OperationCancelled) else "Operation failed")
                        if not isinstance(payload, OperationCancelled) and not self.close_when_idle:
                            messagebox.showerror(self.job_title, str(payload), parent=self)
                    else:
                        self.status_var.set(f"{self.job_title}: complete")
                        if not self.close_when_idle:
                            try:
                                self.job_done(payload)
                            except Exception as exc:
                                self.log(f"Result display failed: {exc}")
                                messagebox.showerror("Result display failed", str(exc), parent=self)
                    if self.close_when_idle:
                        self.destroy()
                        return
            except queue.Empty:
                pass
            self.after(80, self.poll_job)

        def cancel_job(self):
            if self.busy and self.job_control:
                self.job_control.cancelled.set()
                self.status_var.set("Cancel requested; waiting for a safe stopping point")
                self.cancel_button.configure(state="disabled")

        def on_close(self):
            if self.busy:
                self.close_when_idle = True
                self.cancel_job()
            else:
                self.destroy()

        def destroy(self):
            if hasattr(self, "gallery"):
                self.gallery.close()
            # Cancel recurring callbacks before their Tcl commands disappear.
            for after_id in self.tk.call("after", "info"):
                # Each widget owns its callback command; let its destroy remove it.
                self.tk.call("after", "cancel", after_id)
            super().destroy()

        def color(self, name: str) -> str:
            return self.COLORS[name]

        def _load_settings(self) -> None:
            try:
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return
            self.community_var.set(str(data.get("community", "")))
            self.overwrite_var.set(bool(data.get("overwrite", False)))
            self.backup_var.set(bool(data.get("backup_layout", True)))
            self.allow_linked_targets_var.set(bool(data.get("allow_linked_targets", False)))

        def _save_settings(self) -> None:
            data = {
                "community": self.community_var.get().strip(),
                "overwrite": self.overwrite_var.get(),
                "backup_layout": self.backup_var.get(),
                "allow_linked_targets": self.allow_linked_targets_var.get(),
            }
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.status_var.set("Settings saved")
            self.log(f"Settings saved: {self.settings_path}")

        def _build_ui(self):
            build_interface(self)

        def label(self, parent, text, size=10, color=None, weight="normal"):
            return tk.Label(
                parent,
                text=text,
                bg=parent["bg"],
                fg=color or self.color("text"),
                font=("Segoe UI", size, weight),
                anchor="w",
            )

        def button(self, parent, text, command, accent=False, danger=False):
            if danger:
                bg = self.color("red")
                active = self.color("red_hover")
            elif accent:
                bg = self.color("blue")
                active = self.color("blue_hover")
            else:
                bg = self.color("button")
                active = self.color("button_hover")
            return tk.Button(
                parent,
                text=text,
                command=command,
                bg=bg,
                activebackground=active,
                fg="#ffffff",
                activeforeground="#ffffff",
                relief=tk.FLAT,
                bd=0,
                padx=12,
                pady=6,
                cursor="hand2",
                font=("Segoe UI", 9, "bold" if accent or danger else "normal"),
            )

        def entry(self, parent, variable):
            return tk.Entry(
                parent,
                textvariable=variable,
                bg=self.color("field"),
                fg=self.color("text"),
                insertbackground=self.color("text"),
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground=self.color("line_soft"),
                highlightcolor=self.color("cyan"),
                bd=0,
                font=("Segoe UI", 9),
            )









        def checkbutton(self, parent, text, variable):
            return tk.Checkbutton(
                parent,
                text=text,
                variable=variable,
                bg=parent["bg"],
                fg=self.color("text"),
                selectcolor=self.color("field"),
                activebackground=parent["bg"],
                activeforeground=self.color("cyan"),
                relief=tk.FLAT,
                font=("Segoe UI", 10),
            )

        def show_page(self, page_name: str) -> None:
            self.pages[page_name].tkraise()
            for name, button in self.nav_buttons.items():
                active = name == page_name
                button.configure(
                    bg=self.color("sidebar_active") if active else self.color("sidebar"),
                    fg=self.color("cyan") if active else "#c4ccd5",
                    font=("Segoe UI", 10, "bold" if active else "normal"),
                )
            status_name = {"Installed": "Manage", "Liveries": "Install"}.get(page_name, page_name)
            if not self.busy:
                self.status_var.set(f"{status_name} ready")

        def set_text(self, widget, text: str) -> None:
            widget.configure(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            widget.insert(tk.END, text)
            widget.configure(state=tk.NORMAL)

        def get_selected_package(self) -> Path | None:
            selected = self.package_var.get()
            package = self.package_paths.get(selected)
            community = self.community_var.get().strip()
            if package and community and package.parent.resolve() == normalize_path(community):
                return package
            return None

        def describe_package(self, package_root: Path) -> str:
            return diagnose_package(package_root)

        def update_product_views(self) -> None:
            if hasattr(self, "product_listbox"):
                self.product_listbox.delete(0, tk.END)
                for package in self.detected_packages:
                    self.product_listbox.insert(tk.END, package.name)
                if self.detected_packages:
                    selected = self.get_selected_package()
                    index = self.detected_packages.index(selected) if selected in self.detected_packages else 0
                    self.product_listbox.selection_set(index)
                    self.product_listbox.activate(index)
                    self.set_text(self.product_detail_text, "Scanning selected product…")
                else:
                    self.set_text(
                        self.product_detail_text,
                        "No PMDG aircraft packages were detected.\n\nSelect the MSFS 2024 Community folder and refresh products.",
                    )

        def on_product_select(self, _event=None) -> None:
            if self.busy:
                return
            selection = self.product_listbox.curselection()
            if not selection:
                return
            package = self.detected_packages[selection[0]]
            for label_text, package_path in self.package_paths.items():
                if package_path == package:
                    self.package_var.set(label_text)
                    break
            if not self.busy:
                self.refresh_installed_liveries()

        def selected_installed_livery(self) -> InstalledLivery | None:
            if not hasattr(self, "installed_tree"):
                return None
            selection = self.installed_tree.selection()
            if not selection:
                return None
            return self.installed_livery_items.get(selection[0])

        def installed_livery_details(self, livery: InstalledLivery) -> str:
            lines = [
                f"Aircraft: {livery.aircraft_name}",
                f"Livery: {livery.name}",
                f"Registration: {livery.metadata.get('atc_id', 'Not provided')}",
                f"Files: {livery.file_count}",
                f"Folders: {livery.folder_count}",
                f"Size: {format_bytes(livery.total_size)}",
            ]
            if livery.modified_time:
                lines.append(f"Modified: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(livery.modified_time))}")
            if livery.metadata:
                lines.append("")
                lines.append("Metadata:")
                for key in ("title", "ui_variation", "atc_id", "icao_airline", "atc_airline"):
                    if key in livery.metadata:
                        lines.append(f"  {key}: {livery.metadata[key]}")
            lines.extend(["", f"Folder: {livery.path}", f"Thumbnail: {livery.thumbnail_path or 'not found'}"])
            return "\n".join(lines)

        def clear_thumbnail(self, message: str) -> None:
            self.preview_generation = getattr(self, "preview_generation", 0) + 1
            self.thumbnail_image = None
            self.thumbnail_label.configure(image="", text=message, fg=self.color("muted"))

        def show_thumbnail(self, livery: InstalledLivery) -> None:
            self.clear_thumbnail("Loading thumbnail…" if livery.thumbnail_path else "No thumbnail found")
            if not livery.thumbnail_path:
                return
            generation = self.preview_generation
            path = livery.thumbnail_path
            max_width = max(200, self.thumbnail_label.master.winfo_width() - 20)
            def load():
                try:
                    from PIL import Image
                    stat = path.stat()
                    key = (str(path), stat.st_mtime_ns, stat.st_size, max_width)
                    with Image.open(path) as pixels:
                        pixels.thumbnail((max_width, 120))
                        result = pixels.convert("RGBA")
                    self.job_queue.put(("thumbnail", (generation, key, result, None)))
                except Exception as exc:
                    self.job_queue.put(("thumbnail", (generation, None, None, str(exc))))
            threading.Thread(target=load, name="Thumbnail decoder", daemon=True).start()

        def finish_thumbnail(self, payload):
            generation, key, pixels, error = payload
            if generation != self.preview_generation:
                return
            if error:
                self.clear_thumbnail("Thumbnail unavailable")
                self.log(f"Thumbnail preview: {error}")
                return
            from PIL import ImageTk
            image = self.thumbnail_cache.get(key)
            if image is None:
                image = ImageTk.PhotoImage(pixels, master=self)
                if len(self.thumbnail_cache) >= 24:
                    self.thumbnail_cache.pop(next(iter(self.thumbnail_cache)))
                self.thumbnail_cache[key] = image
            self.thumbnail_image = image
            self.thumbnail_label.configure(image=image, text="")

        def refresh_installed_liveries(self) -> None:
            if self.busy:
                return
            package = self.get_selected_package()
            if not package:
                self.installed_liveries = []
                self.render_liveries()
                self.set_text(self.product_detail_text, "Select a Community folder and aircraft package.")
                return

            def scan():
                checkpoint("Scanning installed liveries", force=True)
                return list_installed_liveries(package), self.describe_package(package)

            def complete(result):
                self.installed_liveries, description = result
                self.render_liveries()
                self.set_text(self.product_detail_text, description)
                self.log(f"Found {len(self.installed_liveries)} liveries in {ensure_livery_package_root(package)}")

            self.run_job("Scanning liveries", scan, complete)

        def render_liveries(self):
            if not hasattr(self, "installed_tree"):
                return
            selected = {str(self.installed_livery_items[iid].path) for iid in self.installed_tree.selection()
                        if iid in self.installed_livery_items}
            existing = self.installed_tree.get_children()
            if existing:
                self.installed_tree.delete(*existing)
            self.installed_livery_items.clear()
            visible = filter_liveries(self.installed_liveries, self.search_var.get(), self.thumbnail_filter_var.get())
            selection = []
            for index, livery in enumerate(visible):
                iid = str(index)
                self.installed_livery_items[iid] = livery
                self.installed_tree.insert("", tk.END, iid=iid, values=(
                    livery.aircraft_name, livery.metadata.get("title") or livery.name,
                    livery.file_count, format_bytes(livery.total_size),
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(livery.modified_time))))
                if str(livery.path) in selected:
                    selection.append(iid)
            self.gallery.set_items(self.installed_livery_items)
            self.library_summary.set(f"{len(visible)} of {len(self.installed_liveries)} liveries  ·  Ctrl / Shift to select multiple")
            if visible:
                self.installed_tree.selection_set(selection or ["0"])
                self.on_installed_livery_select()
            else:
                self.set_text(self.installed_detail_text, "No matching liveries. Clear the search/filter or scan another aircraft.")
                self.clear_thumbnail("No matching liveries")
                self.selection_count.set("No livery selected")
            if not self.busy:
                self.status_var.set(f"{len(visible)} of {len(self.installed_liveries)} liveries shown")

        def toggle_library_view(self):
            self.gallery_mode = not self.gallery_mode
            if self.gallery_mode:
                self.table_frame.grid_remove()
                self.gallery.grid(row=0, column=0, sticky="nsew")
            else:
                self.gallery.grid_remove()
                self.table_frame.grid(row=0, column=0, sticky="nsew")

        def on_installed_livery_select(self, _event=None) -> None:
            self.gallery.highlight(self.installed_tree.selection())
            self.selection_count.set(f"{len(self.selected_liveries())} selected")
            livery = self.selected_installed_livery()
            if not livery:
                self.clear_thumbnail("Select a livery")
                self.set_text(self.installed_detail_text, "Select a livery to see its details.")
                return
            self.set_text(self.installed_detail_text, self.installed_livery_details(livery))
            self.show_thumbnail(livery)
            if not self.busy:
                self.status_var.set(f"{len(self.selected_liveries())} selected: {livery.name}")

        def copy_installed_livery_path(self) -> None:
            livery = self.selected_installed_livery()
            if not livery:
                self.status_var.set("No installed livery selected")
                return
            self.clipboard_clear()
            self.clipboard_append(str(livery.path))
            self.status_var.set("Livery path copied")

        def selected_liveries(self):
            return [self.installed_livery_items[iid] for iid in self.installed_tree.selection()
                    if iid in self.installed_livery_items]

        def uninstall_selected_livery(self) -> None:
            package = self.get_selected_package()
            selected = self.selected_liveries()
            if not package or not selected or self.busy:
                return
            names = "\n".join(f"{livery.aircraft_name}/{livery.name}" for livery in selected)
            if not messagebox.askyesno("Uninstall selected liveries", f"Remove {len(selected)} liveries?\n\n{names}\n\nA full recovery backup will be kept.", parent=self):
                return
            backup, linked_targets = self.backup_var.get(), self.allow_linked_targets_var.get()

            def complete(reports):
                text = "\n\n".join(format_uninstall_report(report) for report in reports)
                self.log(text)
                messagebox.showinfo("Uninstall complete", text, parent=self)
                self.refresh_installed_liveries()

            self.run_job("Uninstalling liveries", lambda: uninstall_liveries(
                package, [livery.path for livery in selected], backup, linked_targets), complete)

        def export_selected_liveries(self):
            package, selected = self.get_selected_package(), self.selected_liveries()
            if not package or not selected or self.busy:
                return
            destination = filedialog.asksaveasfilename(title="Export selected liveries", defaultextension=".zip", filetypes=[("ZIP files", "*.zip")], parent=self)
            if destination:
                self.run_job("Exporting liveries", lambda: export_liveries(package, [l.path for l in selected], Path(destination)),
                             lambda path: messagebox.showinfo("Export complete", f"Saved: {path}\nImport this ZIP through Review & Install.", parent=self))

        def restore_backup(self):
            package = self.get_selected_package()
            if not package or self.busy:
                return
            try:
                target = checked_livery_target(package, self.allow_linked_targets_var.get())
            except InstallerError as exc:
                messagebox.showerror("Restore backup", str(exc), parent=self)
                return
            folder = filedialog.askdirectory(title="Choose a backup folder containing recovery.json", initialdir=str(backup_directory(target)), parent=self)
            if not folder:
                return
            if not messagebox.askyesno("Restore complete package", f"Restore this backup?\n{folder}\n\nThis replaces all liveries in:\n{target}\n\nThe current package will also be backed up.", parent=self):
                return
            allow = self.allow_linked_targets_var.get()
            def complete(text):
                self.log(text)
                messagebox.showinfo("Restore complete", text, parent=self)
                self.refresh_installed_liveries()
            self.run_job("Restoring backup", lambda: restore_package_backup(package, Path(folder), allow), complete)

        def run_diagnostics(self) -> None:
            package = self.get_selected_package()
            if not package:
                self.set_text(self.diagnostics_text, "UNKNOWN: Select a Community folder and PMDG aircraft first.")
                return
            allow = self.allow_linked_targets_var.get()
            self.run_job("Checking livery package", lambda: diagnose_package(package, allow),
                         lambda report: self.set_text(self.diagnostics_text, report))

        def export_diagnostics(self):
            text = self.diagnostics_text.get("1.0", tk.END).strip()
            if not text:
                messagebox.showinfo("Export report", "Run Diagnostics first.", parent=self)
                return
            if self.hide_paths_var.get():
                paths = [Path.home(), Path(sys.executable).parent]
                if self.community_var.get().strip():
                    paths.append(Path(self.community_var.get().strip()))
                package = self.get_selected_package()
                if package:
                    target = ensure_livery_package_root(package)
                    paths.extend([target, target.resolve(), backup_directory(target.resolve())])
                text = redact_report(text, paths)
            destination = filedialog.asksaveasfilename(title="Save diagnostic report", defaultextension=".txt", filetypes=[("Text report", "*.txt")], parent=self)
            if destination:
                try:
                    Path(destination).write_text(text + "\n", encoding="utf-8")
                    self.status_var.set("Diagnostic report exported")
                except OSError as exc:
                    messagebox.showerror("Export failed", str(exc), parent=self)

        def rebuild_selected_layout(self) -> None:
            package = self.get_selected_package()
            if not package:
                messagebox.showerror("Missing package", "Select a PMDG package first.", parent=self)
                return
            backup, allow = self.backup_var.get(), self.allow_linked_targets_var.get()
            def complete(result):
                target, count, updated, recovery = result
                text = f"PASS: Rebuilt and verified {count} files.\nLivery package: {target}\nRecovery backup: {recovery}"
                self.log(text)
                self.set_text(self.diagnostics_text, text)
            self.run_job("Rebuilding livery layout", lambda: rebuild_livery_layout(package, backup, allow), complete)

        def log(self, message: str) -> None:
            self.log_text.insert(tk.END, message.rstrip() + "\n")
            self.log_text.see(tk.END)

        def detect_paths(self) -> None:
            def complete(candidates):
                for candidate in candidates:
                    self.log(f"Community: {candidate.path}\n  Config: {', '.join(map(str, candidate.configs))}\n  Products: {', '.join(candidate.products) or 'none'}\n  Writable (estimate): {candidate.writable}")
                current = self.community_var.get().strip()
                if len(candidates) == 1 and not current:
                    self.community_var.set(str(candidates[0].path))
                    self.refresh_packages()
                elif len(candidates) > 1:
                    self.choose_detected_path(candidates)
                else:
                    if not candidates:
                        self.log("No configured Community folder found. Browse to the MSFS 2024 Community folder.")
                    self.refresh_packages()
            self.run_job("Detecting simulator paths", community_candidates, complete)

        def choose_detected_path(self, candidates):
            dialog = tk.Toplevel(self)
            dialog.title("Choose the MSFS 2024 Community folder")
            dialog.geometry("900x430")
            dialog.transient(self)
            dialog.grab_set()
            tk.Label(dialog, text="Multiple locations found. Select the one used by your MSFS 2024 installation.", anchor="w").pack(fill=tk.X, padx=12, pady=12)
            choices = tk.Listbox(dialog, height=5, exportselection=False)
            choices.pack(fill=tk.X, padx=12)
            details = tk.Text(dialog, height=9, wrap="word")
            details.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)
            for candidate in candidates:
                choices.insert(tk.END, str(candidate.path))
            def show(_event=None):
                selection = choices.curselection()
                if selection:
                    c = candidates[selection[0]]
                    self.set_text(details, f"Path: {c.path}\nConfiguration: {', '.join(map(str, c.configs))}\nDetected products: {', '.join(c.products) or 'none'}\nWritable (OS estimate): {c.writable}")
            choices.bind("<<ListboxSelect>>", show)
            current = self.community_var.get().strip()
            matching = next((i for i, c in enumerate(candidates) if str(c.path).casefold() == current.casefold()), 0)
            choices.selection_set(matching)
            show()
            def accept():
                selection = choices.curselection()
                if selection:
                    self.community_var.set(str(candidates[selection[0]].path))
                    dialog.destroy()
                    self.refresh_packages()
            tk.Button(dialog, text="Use Selected Folder", command=accept).pack(pady=(0, 12))

        def refresh_packages(self) -> None:
            if self.busy:
                return
            community = self.community_var.get().strip()
            if not community:
                self.apply_packages([])
                return
            self.run_job("Scanning PMDG products", lambda: find_pmdg_product_roots(Path(community)), self.apply_packages)

        def apply_packages(self, packages):
            self.package_paths.clear()
            self.detected_packages = packages
            values = []
            for package in packages:
                aircraft = known_airplane_folder_name(package)
                label = f"{aircraft or package.name}  ·  {package.name}"
                self.package_paths[label] = package
                values.append(label)
            self.package_combo["values"] = values
            self.installed_package_combo["values"] = values
            if self.package_var.get() not in values:
                self.package_var.set(values[0] if values else "")
            self.package_count_var.set(f"{len(values)} products detected")
            self.update_product_views()
            self.refresh_installed_liveries()

        def choose_community(self) -> None:
            path = filedialog.askdirectory(title="Select MSFS 2024 Community folder")
            if path:
                self.community_var.set(path)
                self.status_var.set("Community folder selected")
                self.refresh_packages()

        def choose_zip(self) -> None:
            path = filedialog.askopenfilename(
                title="Select livery ZIP",
                filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")],
            )
            if path:
                self.livery_var.set(path)
                self.status_var.set("Livery package selected")

        def choose_livery_folder(self) -> None:
            path = filedialog.askdirectory(title="Select extracted livery folder")
            if path:
                self.livery_var.set(path)
                self.status_var.set("Livery folder selected")

        def install_selected(self) -> None:
            package_root = self.get_selected_package()
            livery_path = self.livery_var.get().strip()
            if not package_root or not livery_path:
                messagebox.showerror("Missing selection", "Select a PMDG aircraft and livery ZIP/folder first.", parent=self)
                return
            overwrite, backup, allow = self.overwrite_var.get(), self.backup_var.get(), self.allow_linked_targets_var.get()
            source = Path(livery_path)

            def review(plan):
                summary = format_install_plan(plan)
                self.log(summary)
                dialog = tk.Toplevel(self)
                dialog.title("Review livery installation")
                dialog.geometry("850x560")
                dialog.configure(bg=self.color("bg"))
                dialog.transient(self)
                dialog.grab_set()
                if plan.preview_png:
                    preview_image = tk.PhotoImage(data=plan.preview_png, master=dialog)
                    preview_label = tk.Label(dialog, image=preview_image, text="First detected thumbnail", compound=tk.TOP,
                                             bg=self.color("bg"), fg=self.color("muted"))
                    preview_label.image = preview_image
                    preview_label.pack(pady=6)
                from livery_ui import text_area
                report = text_area(dialog)
                report.insert("1.0", summary)
                report.configure(state="disabled")
                actions = tk.Frame(dialog, bg=self.color("bg"))
                actions.pack(fill=tk.X, padx=12, pady=12)
                self.button(actions, "Close", dialog.destroy).pack(side=tk.RIGHT)
                def install():
                    dialog.destroy()
                    self.run_job("Installing livery", lambda: install_livery(source, package_root, overwrite, backup, allow), complete)
                if plan.conflicts and not overwrite:
                    self.label(actions, "Conflicts found. Close, review Allow replacement, then try again.").pack(side=tk.LEFT)
                else:
                    self.button(actions, "Install These Liveries", install, accent=True).pack(side=tk.LEFT)

            def complete(report):
                text = format_report(report)
                self.log(text)
                messagebox.showinfo("Files installed and verified", text, parent=self)
                self.refresh_installed_liveries()

            self.run_job("Inspecting livery source", lambda: preview_install(source, package_root, overwrite, allow), review)

    app = InstallerApp()
    if run_mainloop:
        app.mainloop()
    return app


def build_parser() -> argparse.ArgumentParser:
    from app_version import VERSION
    parser = argparse.ArgumentParser(
        description="Install PMDG MSFS 2024 liveries without PMDG OC3.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s v{VERSION}")
    parser.add_argument("--detect", action="store_true", help="Print detected MSFS 2024 paths.")
    parser.add_argument("--community", type=Path, help="MSFS 2024 Community folder.")
    parser.add_argument("--package", help="PMDG package folder name, e.g. pmdg-aircraft-738.")
    parser.add_argument("--package-root", type=Path, help="Full PMDG package folder path.")
    parser.add_argument("--livery", type=Path, help="Livery ZIP or extracted livery folder.")
    parser.add_argument("--preflight", action="store_true", help="Inspect --livery and show targets/conflicts without installing.")
    parser.add_argument("--diagnose", action="store_true", help="Check the companion livery package and print actions.")
    parser.add_argument("--rebuild-layout", action="store_true", help="Rebuild and verify the companion livery package index.")
    parser.add_argument("--restore-backup", type=Path, help="Restore a full recovery folder containing recovery.json.")
    parser.add_argument("--export-zip", type=Path, help="Export all (or searched) liveries to a ZIP outside the package.")
    parser.add_argument("--search", default="", help="Filter --list-liveries or --export-zip by name/airline/registration.")
    parser.add_argument("--list-liveries", action="store_true", help="List installed liveries for the selected PMDG package.")
    parser.add_argument("--uninstall-livery", help="Uninstall an installed livery by folder name, Aircraft/Livery name, or full folder path.")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting existing files.")
    parser.add_argument("--no-backup", action="store_true", help="Do not back up layout.json.")
    parser.add_argument("--allow-linked-targets", action="store_true", help="Allow writing into symlink/junction livery targets.")
    parser.add_argument("--gui", action="store_true", help="Launch the GUI.")
    return parser


def resolve_package_from_args(args: argparse.Namespace) -> Path:
    if args.package_root:
        return validate_selected_package_root(args.package_root)

    if not args.community or not args.package:
        raise InstallerError("Use --package-root, or use --community with --package.")

    package_root = normalize_path(args.community) / args.package
    return validate_selected_package_root(package_root)


def print_detected_paths() -> None:
    detected = detect_msfs2024_paths()
    print("UserCfg.opt:")
    for path in detected.user_cfg_paths:
        print(f"  {path}")
    print("Community:")
    for path in detected.community_paths:
        print(f"  {path}")
    if detected.community_paths:
        print("PMDG packages:")
        for community in detected.community_paths:
            for package in find_pmdg_product_roots(community):
                print(f"  {package}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(arguments)

    if not arguments or args.gui:
        launch_gui()
        return 0

    if args.detect:
        print_detected_paths()
        return 0

    if args.preflight or args.diagnose or args.rebuild_layout or args.restore_backup or args.export_zip:
        try:
            package = resolve_package_from_args(args)
            if args.preflight:
                if not args.livery:
                    parser.error("--preflight requires --livery")
                print(format_install_plan(preview_install(args.livery, package, args.overwrite, args.allow_linked_targets)))
            elif args.diagnose:
                report = diagnose_package(package, args.allow_linked_targets)
                print(report)
                return 2 if "FAIL:" in report else 0
            elif args.rebuild_layout:
                target, count, updated, recovery = rebuild_livery_layout(package, not args.no_backup, args.allow_linked_targets)
                print(f"Verified {count} files in {target}\nRecovery backup: {recovery}")
            elif args.restore_backup:
                print(restore_package_backup(package, args.restore_backup, args.allow_linked_targets))
            else:
                liveries = filter_liveries(list_installed_liveries(package), args.search)
                if args.export_zip.exists() and not args.overwrite:
                    raise InstallerError("Export already exists. Use --overwrite to replace it.")
                print(export_liveries(package, [livery.path for livery in liveries], args.export_zip))
        except (InstallerError, OSError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.list_liveries:
        try:
            package_root = resolve_package_from_args(args)
            liveries = filter_liveries(list_installed_liveries(package_root), args.search)
        except (InstallerError, OSError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        for livery in liveries:
            print(
                "\t".join(
                    [
                        livery.aircraft_name,
                        livery.name,
                        str(livery.path),
                        str(livery.thumbnail_path or ""),
                    ]
                )
            )
        return 0

    if args.uninstall_livery:
        try:
            package_root = resolve_package_from_args(args)
            report = uninstall_livery(
                package_root,
                args.uninstall_livery,
                backup_layout=not args.no_backup,
                allow_linked_targets=args.allow_linked_targets,
            )
        except (InstallerError, OSError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(format_uninstall_report(report))
        return 0

    if not args.livery:
        parser.error("--livery is required unless --detect or --gui is used.")

    try:
        package_root = resolve_package_from_args(args)
        report = install_livery(
            args.livery,
            package_root,
            overwrite=args.overwrite,
            backup_layout=not args.no_backup,
            allow_linked_targets=args.allow_linked_targets,
        )
    except (InstallerError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

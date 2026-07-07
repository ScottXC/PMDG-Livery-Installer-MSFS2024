#!/usr/bin/env python3
"""
PMDG Livery Installer for MSFS 2024.

Installs MSFS 2024 PMDG livery ZIP/folder packages without PMDG OC3 by
copying the livery files into an existing PMDG aircraft package and rebuilding
layout.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable


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


class InstallerError(RuntimeError):
    """Raised for user-fixable installer failures."""


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
    local_appdata = Path(os.environ.get("LOCALAPPDATA", ""))
    appdata = Path(os.environ.get("APPDATA", ""))

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
    if not community_path.exists():
        return []

    packages: list[Path] = []
    for child in community_path.iterdir():
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

    if community_path.exists():
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
    package_root = normalize_path(package_root)
    if package_root.exists():
        return validate_package_root(package_root)
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
                raw_name = item.filename.replace("\\", "/")
                rel = PurePosixPath(raw_name)
                if rel.is_absolute() or ".." in rel.parts:
                    raise InstallerError(f"Unsafe path in ZIP: {item.filename}")
                if not rel.name:
                    continue
                destination = target_dir.joinpath(*rel.parts)
                if item.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, destination.open("wb") as dest:
                    shutil.copyfileobj(source, dest)
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
            yield tmp_path
            return
        except OSError as exc:
            last_error = exc
            continue
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    raise InstallerError(f"Cannot create a writable temporary folder: {last_error}")


def iter_dirs(root: Path) -> Iterable[Path]:
    for current, dirnames, _ in os.walk(root):
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
    selected_package_root = validate_selected_package_root(package_root)
    livery_package_root = ensure_livery_package_root(selected_package_root)
    if not livery_package_root.exists():
        raise InstallerError(f"Livery package does not exist: {livery_package_root}")
    if is_reparse_point(livery_package_root) and not allow_linked_targets:
        raise InstallerError(
            "The target livery package is a symlink/junction/reparse-point folder. "
            "Uninstall is blocked by default to avoid deleting files from a linked source folder."
        )

    livery = resolve_installed_livery(selected_package_root, livery_identifier)
    livery_root = normalize_path(livery.path)
    if is_reparse_point(livery_root):
        raise InstallerError("The selected livery folder is a symlink/junction/reparse-point and was not removed.")
    if not livery_root.exists() or not livery_root.is_dir():
        raise InstallerError(f"Installed livery folder does not exist: {livery_root}")

    file_count, folder_count, total_size = count_folder_contents(livery_root)
    try:
        shutil.rmtree(livery_root)
    except OSError as exc:
        raise InstallerError(f"Could not remove livery folder: {exc}") from exc

    layout_entries, manifest_updated, backup_path = rebuild_layout(
        livery_package_root,
        backup=backup_layout,
    )
    return UninstallReport(
        package_root=livery_package_root,
        livery_path=livery_root,
        aircraft_name=livery.aircraft_name,
        livery_name=livery.name,
        removed_files=file_count,
        removed_dirs=folder_count,
        removed_size=total_size,
        layout_entries=layout_entries,
        manifest_updated=manifest_updated,
        backup_path=backup_path,
    )


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
    livery_airplanes = livery_package_root / "SimObjects" / "Airplanes"
    if livery_airplanes.exists():
        livery_folders = [p for p in livery_airplanes.iterdir() if p.is_dir()]
        if len(livery_folders) == 1:
            return livery_folders[0].name

    known_folder = known_airplane_folder_name(selected_package_root)
    if known_folder:
        return known_folder

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

    if backup and layout_path.exists():
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup_path = package_root / f"layout.json.bak-{timestamp}"
        shutil.copy2(layout_path, backup_path)

    stale_generator = package_root / "MSFSLayoutGenerator.exe"
    if stale_generator.exists():
        stale_generator.unlink()

    generator_path = layout_generator_path()
    try:
        subprocess.run(
            [str(generator_path), str(layout_path)],
            cwd=str(package_root),
            check=True,
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if detail:
            raise InstallerError(f"MSFSLayoutGenerator failed: {detail}") from exc
        raise InstallerError(f"MSFSLayoutGenerator failed with exit code {exc.returncode}") from exc

    try:
        data = json.loads(layout_path.read_text(encoding="utf-8-sig"))
        content = data.get("content", [])
        entry_count = len(content) if isinstance(content, list) else 0
    except (OSError, json.JSONDecodeError):
        entry_count = 0
    return entry_count, False, backup_path


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
    livery_package_root = normalize_path(livery_package_root)

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


def install_livery(
    livery_input: Path,
    package_root: Path,
    overwrite: bool = False,
    backup_layout: bool = True,
    allow_linked_targets: bool = False,
) -> InstallReport:
    selected_package_root = validate_selected_package_root(package_root)
    livery_package_root = ensure_livery_package_root(selected_package_root)

    with temporary_workspace(livery_package_root) as tmp:
        source_root = source_root_from_input(livery_input, tmp)
        validate_install_safety(
            livery_input,
            source_root,
            selected_package_root,
            livery_package_root,
            allow_linked_targets=allow_linked_targets,
        )

        livery_package_roots = find_livery_package_roots(source_root)
        if livery_package_roots:
            source_package_root = livery_package_roots[0]
            if source_package_root.name.lower() != livery_package_root.name.lower():
                raise InstallerError(
                    "The livery package does not match the selected aircraft package. "
                    f"Source is {source_package_root.name}, target is {livery_package_root.name}."
                )
            livery_package_root.mkdir(parents=True, exist_ok=True)
            copied_files, copied_dirs, installed_roots = copy_livery_package_contents(
                source_package_root,
                livery_package_root,
                overwrite=overwrite,
            )
            ensure_livery_package_skeleton(livery_package_root, selected_package_root)
        else:
            source_package_root = None
            ensure_livery_package_skeleton(livery_package_root, selected_package_root)
            simobjects_roots = find_simobjects_roots(source_root)
            if simobjects_roots:
                copy_root = simobjects_roots[0]
                copied_files, copied_dirs, installed_roots = copy_package_contents(
                    copy_root,
                    livery_package_root,
                    overwrite=overwrite,
                )
            else:
                livery_folders = find_direct_livery_folders(source_root)
                if not livery_folders:
                    raise InstallerError(
                        "No installable livery structure found. Expected a PMDG "
                        "*-liveries package, a SimObjects folder, or a livery folder "
                        "containing livery.cfg/texture/model/panel."
                    )
                copied_files, copied_dirs, installed_roots = copy_direct_liveries(
                    livery_folders,
                    selected_package_root,
                    livery_package_root,
                    overwrite=overwrite,
                )

    layout_entries, manifest_updated, backup_path = rebuild_layout(
        livery_package_root,
        backup=backup_layout,
    )
    return InstallReport(
        package_root=livery_package_root,
        source_package_root=source_package_root,
        copied_files=copied_files,
        copied_dirs=copied_dirs,
        layout_entries=layout_entries,
        manifest_updated=manifest_updated,
        backup_path=backup_path,
        installed_roots=installed_roots,
    )


def format_report(report: InstallReport) -> str:
    lines = [
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
    return "\n".join(lines)


def launch_gui() -> None:
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

    class InstallerApp(tk.Tk):
        COLORS = {
            "bg": "#19211f",
            "top": "#111817",
            "sidebar": "#141b19",
            "sidebar_active": "#24342f",
            "panel": "#242d2a",
            "panel_alt": "#2d3834",
            "field": "#171e1c",
            "log": "#121715",
            "line": "#34423d",
            "line_soft": "#29342f",
            "muted": "#a8b3ad",
            "text": "#f2f4ef",
            "red": "#f05262",
            "red_hover": "#ff6a55",
            "amber": "#e7bd55",
            "green": "#62d08c",
            "cyan": "#36b6cf",
            "blue": "#3578c6",
            "button": "#2d3935",
            "button_hover": "#3a4943",
        }

        def __init__(self) -> None:
            super().__init__()
            self.title("PMDG Livery Installer MSFS2024")
            self.geometry("1280x780")
            self.minsize(1120, 680)
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
            self.settings_path = (
                Path(os.environ.get("APPDATA", Path.home()))
                / "PMDG Livery Installer MSFS2024"
                / "settings.json"
            )
            self._load_settings()

            self._build_ui()
            self.detect_paths()
            self.show_page("Products")

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

        def _build_ui(self) -> None:
            self.option_add("*Font", ("Segoe UI", 10))
            self.option_add("*TCombobox*Listbox.background", self.color("field"))
            self.option_add("*TCombobox*Listbox.foreground", self.color("text"))
            self.option_add("*TCombobox*Listbox.selectBackground", self.color("blue"))
            self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

            style = ttk.Style(self)
            style.theme_use("clam")
            style.configure(
                "PMDG.TCombobox",
                fieldbackground=self.color("field"),
                background=self.color("field"),
                foreground=self.color("text"),
                bordercolor=self.color("line_soft"),
                lightcolor=self.color("line_soft"),
                darkcolor=self.color("line_soft"),
                arrowcolor=self.color("cyan"),
                padding=5,
            )
            style.configure(
                "PMDG.Treeview",
                background=self.color("log"),
                fieldbackground=self.color("log"),
                foreground=self.color("text"),
                bordercolor=self.color("line"),
                lightcolor=self.color("line"),
                darkcolor=self.color("line"),
                rowheight=24,
                font=("Segoe UI", 9),
            )
            style.configure(
                "PMDG.Treeview.Heading",
                background=self.color("panel_alt"),
                foreground=self.color("text"),
                bordercolor=self.color("line"),
                relief=tk.FLAT,
                font=("Segoe UI", 9, "bold"),
            )
            style.map(
                "PMDG.Treeview",
                background=[("selected", self.color("blue"))],
                foreground=[("selected", "#ffffff")],
            )
            style.configure(
                "PMDG.Vertical.TScrollbar",
                background=self.color("panel_alt"),
                troughcolor=self.color("log"),
                bordercolor=self.color("log"),
                arrowcolor=self.color("muted"),
                relief=tk.FLAT,
            )

            topbar = tk.Frame(self, bg=self.color("top"), height=72)
            topbar.pack(fill=tk.X)
            topbar.pack_propagate(False)

            brand = tk.Frame(topbar, bg=self.color("top"))
            brand.pack(side=tk.LEFT, padx=16, pady=12)
            tk.Label(
                brand,
                text="PMDG",
                bg=self.color("panel_alt"),
                fg="#ffffff",
                font=("Segoe UI", 11, "bold"),
                width=6,
                pady=6,
            ).pack(side=tk.LEFT)
            tk.Frame(brand, bg=self.color("cyan"), width=3, height=30).pack(side=tk.LEFT, padx=(0, 10))
            title_box = tk.Frame(brand, bg=self.color("top"))
            title_box.pack(side=tk.LEFT)
            tk.Label(
                title_box,
                text="PMDG Livery Installer MSFS2024",
                bg=self.color("top"),
                fg="#ffffff",
                font=("Segoe UI Semibold", 13),
                anchor="w",
            ).pack(anchor="w")
            tk.Label(
                title_box,
                text="Manual livery management for PMDG aircraft packages",
                bg=self.color("top"),
                fg=self.color("muted"),
                font=("Segoe UI", 8),
                anchor="w",
            ).pack(anchor="w", pady=(2, 0))
            tk.Label(
                topbar,
                textvariable=self.status_var,
                bg=self.color("panel"),
                fg=self.color("green"),
                font=("Segoe UI", 9),
                anchor="e",
                padx=10,
                pady=5,
            ).pack(side=tk.RIGHT, padx=16)
            tk.Frame(self, bg=self.color("cyan"), height=2).pack(fill=tk.X)

            body = tk.Frame(self, bg=self.color("bg"))
            body.pack(fill=tk.BOTH, expand=True)

            sidebar = tk.Frame(body, bg=self.color("sidebar"), width=172)
            sidebar.pack(side=tk.LEFT, fill=tk.Y)
            sidebar.pack_propagate(False)
            tk.Label(
                sidebar,
                text="OPERATIONS",
                bg=self.color("sidebar"),
                fg=self.color("cyan"),
                font=("Segoe UI", 8, "bold"),
                anchor="w",
            ).pack(fill=tk.X, padx=14, pady=(14, 6))

            for page_name in ("Products", "Installed", "Liveries", "Diagnostics", "Settings"):
                nav = tk.Button(
                    sidebar,
                    text=page_name,
                    command=lambda name=page_name: self.show_page(name),
                    bg=self.color("sidebar"),
                    activebackground=self.color("sidebar_active"),
                    fg="#c4ccd5",
                    activeforeground="#ffffff",
                    relief=tk.FLAT,
                    bd=0,
                    cursor="hand2",
                    font=("Segoe UI", 9),
                    anchor="w",
                    padx=14,
                    pady=8,
                )
                nav.pack(fill=tk.X, pady=(1, 0))
                self.nav_buttons[page_name] = nav
            tk.Label(
                sidebar,
                text="MSFS 2024\nCommunity Package Mode",
                bg=self.color("sidebar"),
                fg=self.color("muted"),
                justify=tk.LEFT,
                font=("Segoe UI", 8),
                anchor="sw",
            ).pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=14)

            self.page_container = tk.Frame(body, bg=self.color("bg"))
            self.page_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=12, pady=12)
            self.page_container.rowconfigure(0, weight=1)
            self.page_container.columnconfigure(0, weight=1)

            for page_name, factory in (
                ("Products", self._create_products_page),
                ("Installed", self._create_installed_page),
                ("Liveries", self._create_liveries_page),
                ("Diagnostics", self._create_diagnostics_page),
                ("Settings", self._create_settings_page),
            ):
                page = factory()
                page.grid(row=0, column=0, sticky="nsew")
                self.pages[page_name] = page

        def label(self, parent, text, size=10, color=None, weight="normal"):
            return tk.Label(
                parent,
                text=text,
                bg=parent["bg"],
                fg=color or self.color("text"),
                font=("Segoe UI", size, weight),
                anchor="w",
            )

        def button(self, parent, text, command, accent=False):
            bg = self.color("red") if accent else self.color("button")
            active = self.color("red_hover") if accent else self.color("button_hover")
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
                font=("Segoe UI", 9, "bold" if accent else "normal"),
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

        def card(self, parent, title, subtitle=None):
            frame = tk.Frame(parent, bg=self.color("panel"), highlightthickness=0)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(2, weight=1)
            tk.Frame(frame, bg=self.color("cyan"), height=1).grid(row=0, column=0, sticky="ew")
            header = tk.Frame(frame, bg=self.color("panel"))
            header.grid(row=1, column=0, sticky="ew", padx=12, pady=(8, 4))
            self.label(header, title, 10, self.color("text"), "bold").pack(anchor="w")
            content = tk.Frame(frame, bg=self.color("panel"))
            content.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
            content.columnconfigure(0, weight=1)
            return frame, content

        def make_page(self, headline, subhead):
            page = tk.Frame(self.page_container, bg=self.color("bg"))
            page.rowconfigure(1, weight=1)
            page.columnconfigure(0, weight=1)
            header = tk.Frame(page, bg=self.color("panel_alt"), highlightthickness=0)
            header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
            header.columnconfigure(1, weight=1)
            tk.Frame(header, bg=self.color("blue"), width=5).grid(row=0, column=0, rowspan=2, sticky="ns")
            self.label(header, headline.upper(), 8, self.color("cyan"), "bold").grid(row=0, column=1, sticky="w", padx=12, pady=(8, 1))
            self.label(header, subhead, 9, self.color("text")).grid(row=1, column=1, sticky="w", padx=12, pady=(0, 8))
            return page

        def _create_products_page(self):
            page = self.make_page("Products", "Scan installed PMDG packages and inspect package health.")
            content = tk.Frame(page, bg=self.color("bg"))
            content.grid(row=1, column=0, sticky="nsew")
            content.rowconfigure(1, weight=1)
            content.columnconfigure(0, weight=1)
            content.columnconfigure(1, weight=2)

            summary_card, summary = self.card(content, "Product Library", "Community packages that look like PMDG aircraft products.")
            summary_card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
            summary.columnconfigure(0, weight=1)
            tk.Label(
                summary,
                textvariable=self.package_count_var,
                bg=self.color("panel"),
                fg=self.color("amber"),
                font=("Segoe UI Semibold", 18),
                anchor="w",
            ).grid(row=0, column=0, sticky="ew")
            actions = tk.Frame(summary, bg=self.color("panel"))
            actions.grid(row=0, column=1, sticky="e")
            self.button(actions, "Detect Paths", self.detect_paths).pack(side=tk.LEFT)
            self.button(actions, "Refresh Products", self.refresh_packages, accent=True).pack(side=tk.LEFT, padx=(10, 0))

            list_card, list_body = self.card(content, "Installed Products", "Select a product to view package details.")
            list_card.grid(row=1, column=0, sticky="nsew", padx=(0, 7))
            list_card.rowconfigure(1, weight=1)
            list_body.rowconfigure(0, weight=1)
            self.product_listbox = tk.Listbox(
                list_body,
                bg=self.color("log"),
                fg=self.color("text"),
                selectbackground=self.color("blue"),
                selectforeground="#ffffff",
                relief=tk.FLAT,
                bd=0,
                highlightthickness=0,
                font=("Segoe UI", 10),
                activestyle="none",
            )
            self.product_listbox.grid(row=0, column=0, sticky="nsew")
            product_scrollbar = ttk.Scrollbar(list_body, orient=tk.VERTICAL, command=self.product_listbox.yview, style="PMDG.Vertical.TScrollbar")
            product_scrollbar.grid(row=0, column=1, sticky="ns")
            self.product_listbox.configure(yscrollcommand=product_scrollbar.set)
            self.product_listbox.bind("<<ListboxSelect>>", self.on_product_select)

            detail_card, detail_body = self.card(content, "Product Details", "Manifest, layout, aircraft folders and livery inventory.")
            detail_card.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
            detail_card.rowconfigure(1, weight=1)
            detail_body.rowconfigure(0, weight=1)
            self.product_detail_text = tk.Text(
                detail_body,
                bg=self.color("log"),
                fg="#cfd7df",
                relief=tk.FLAT,
                bd=0,
                padx=10,
                pady=8,
                wrap="word",
                font=("Consolas", 9),
            )
            self.product_detail_text.grid(row=0, column=0, sticky="nsew")
            detail_scrollbar = ttk.Scrollbar(detail_body, orient=tk.VERTICAL, command=self.product_detail_text.yview, style="PMDG.Vertical.TScrollbar")
            detail_scrollbar.grid(row=0, column=1, sticky="ns")
            self.product_detail_text.configure(yscrollcommand=detail_scrollbar.set)
            return page

        def _create_installed_page(self):
            page = self.make_page("Installed", "Manage liveries in the companion Community livery package.")
            page.rowconfigure(1, weight=1)
            content = tk.Frame(page, bg=self.color("bg"))
            content.grid(row=1, column=0, sticky="nsew")
            content.columnconfigure(0, weight=3)
            content.columnconfigure(1, weight=2)
            content.rowconfigure(1, weight=1)

            package_card, package = self.card(content, "Aircraft Package", "Choose a PMDG product and scan its installed livery package.")
            package_card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
            package.columnconfigure(0, weight=1)
            self.installed_package_combo = ttk.Combobox(package, textvariable=self.package_var, state="readonly", style="PMDG.TCombobox")
            self.installed_package_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8), ipady=2)
            self.installed_package_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_installed_liveries())
            self.button(package, "Refresh Products", self.refresh_packages).grid(row=0, column=1, padx=(0, 8))
            self.button(package, "Scan Liveries", self.refresh_installed_liveries, accent=True).grid(row=0, column=2)

            list_card, list_body = self.card(content, "Installed Liveries", "Select a livery to inspect its files and thumbnail.")
            list_card.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
            list_card.rowconfigure(1, weight=1)
            list_body.rowconfigure(0, weight=1)
            list_body.columnconfigure(0, weight=1)
            self.installed_tree = ttk.Treeview(
                list_body,
                columns=("aircraft", "livery", "files", "size", "modified"),
                show="headings",
                selectmode="browse",
                style="PMDG.Treeview",
            )
            self.installed_tree.heading("aircraft", text="Aircraft")
            self.installed_tree.heading("livery", text="Livery")
            self.installed_tree.heading("files", text="Files")
            self.installed_tree.heading("size", text="Size")
            self.installed_tree.heading("modified", text="Modified")
            self.installed_tree.column("aircraft", width=155, minwidth=120, stretch=False)
            self.installed_tree.column("livery", width=320, minwidth=180, stretch=True)
            self.installed_tree.column("files", width=70, minwidth=60, anchor="e", stretch=False)
            self.installed_tree.column("size", width=95, minwidth=80, anchor="e", stretch=False)
            self.installed_tree.column("modified", width=150, minwidth=130, stretch=False)
            self.installed_tree.grid(row=0, column=0, sticky="nsew")
            installed_scrollbar = ttk.Scrollbar(list_body, orient=tk.VERTICAL, command=self.installed_tree.yview, style="PMDG.Vertical.TScrollbar")
            installed_scrollbar.grid(row=0, column=1, sticky="ns")
            self.installed_tree.configure(yscrollcommand=installed_scrollbar.set)
            self.installed_tree.bind("<<TreeviewSelect>>", self.on_installed_livery_select)

            preview_card, preview = self.card(content, "Preview", "Thumbnail, metadata and uninstall controls for the selected livery.")
            preview_card.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
            preview_card.rowconfigure(1, weight=1)
            preview.columnconfigure(0, weight=1)
            preview.rowconfigure(1, weight=1)
            preview_shell = tk.Frame(
                preview,
                bg=self.color("log"),
                height=190,
                highlightthickness=0,
            )
            preview_shell.grid(row=0, column=0, sticky="ew", pady=(0, 8))
            preview_shell.grid_propagate(False)
            self.thumbnail_label = tk.Label(
                preview_shell,
                text="No livery selected",
                bg=self.color("log"),
                fg=self.color("muted"),
                justify=tk.CENTER,
                font=("Segoe UI", 10),
            )
            self.thumbnail_label.place(relx=0.5, rely=0.5, anchor="center")

            self.installed_detail_text = tk.Text(
                preview,
                height=7,
                bg=self.color("log"),
                fg="#cfd7df",
                relief=tk.FLAT,
                bd=0,
                padx=10,
                pady=8,
                wrap="word",
                font=("Consolas", 9),
            )
            self.installed_detail_text.grid(row=1, column=0, sticky="nsew")
            detail_scrollbar = ttk.Scrollbar(preview, orient=tk.VERTICAL, command=self.installed_detail_text.yview, style="PMDG.Vertical.TScrollbar")
            detail_scrollbar.grid(row=1, column=1, sticky="ns")
            self.installed_detail_text.configure(yscrollcommand=detail_scrollbar.set)

            actions = tk.Frame(preview, bg=self.color("panel"))
            actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
            self.button(actions, "Copy Path", self.copy_installed_livery_path).pack(side=tk.LEFT)
            self.button(actions, "Refresh", self.refresh_installed_liveries).pack(side=tk.LEFT, padx=(10, 0))
            self.button(actions, "Uninstall Selected", self.uninstall_selected_livery, accent=True).pack(side=tk.RIGHT)
            return page

        def _create_liveries_page(self):
            page = self.make_page("Liveries", "Install third-party PMDG livery packages without using PMDG OC3.")
            page.rowconfigure(1, weight=1)
            content = tk.Frame(page, bg=self.color("bg"))
            content.grid(row=1, column=0, sticky="nsew")
            content.columnconfigure(0, weight=1)
            content.columnconfigure(1, weight=1)
            content.rowconfigure(3, weight=1)

            paths_card, paths = self.card(content, "Simulator Paths", "Detected automatically; override when needed.")
            paths_card.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=(0, 8))
            paths.columnconfigure(1, weight=1)
            self.label(paths, "Community folder", 8, self.color("muted"), "bold").grid(row=0, column=0, sticky="w", pady=(2, 4))
            self.entry(paths, self.community_var).grid(row=0, column=1, sticky="ew", pady=(2, 4), ipady=4)
            self.button(paths, "Browse", self.choose_community).grid(row=0, column=2, padx=(8, 0), pady=(2, 4))

            package_card, package = self.card(content, "Aircraft Package", "Choose the PMDG product to receive the livery.")
            package_card.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=(0, 8))
            package.columnconfigure(0, weight=1)
            tk.Label(
                package,
                textvariable=self.package_count_var,
                bg=self.color("panel"),
                fg=self.color("amber"),
                font=("Segoe UI Semibold", 16),
                anchor="w",
            ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 5))
            self.package_combo = ttk.Combobox(package, textvariable=self.package_var, state="readonly", style="PMDG.TCombobox")
            self.package_combo.grid(row=1, column=0, sticky="ew", padx=(0, 6), ipady=2)
            self.button(package, "Refresh", self.refresh_packages).grid(row=1, column=1)

            install_card, install = self.card(content, "Livery Package", "Select a ZIP or extracted livery folder.")
            install_card.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
            install.columnconfigure(1, weight=1)
            self.label(install, "Source", 8, self.color("muted"), "bold").grid(row=0, column=0, sticky="w", pady=(0, 8))
            self.entry(install, self.livery_var).grid(row=0, column=1, sticky="ew", pady=(0, 8), ipady=4)
            browse_menu = tk.Frame(install, bg=self.color("panel"))
            browse_menu.grid(row=0, column=2, sticky="e", padx=(8, 0), pady=(0, 8))
            self.button(browse_menu, "ZIP", self.choose_zip).pack(side=tk.LEFT)
            self.button(browse_menu, "Folder", self.choose_livery_folder).pack(side=tk.LEFT, padx=(8, 0))
            options = tk.Frame(install, bg=self.color("panel"))
            options.grid(row=1, column=1, sticky="w")
            self.checkbutton(options, "Allow overwrite", self.overwrite_var).pack(side=tk.LEFT)
            self.checkbutton(options, "Backup layout.json", self.backup_var).pack(side=tk.LEFT, padx=(12, 0))
            self.checkbutton(options, "Allow linked targets", self.allow_linked_targets_var).pack(side=tk.LEFT, padx=(12, 0))
            action_bar = tk.Frame(install, bg=self.color("panel"))
            action_bar.grid(row=1, column=2, sticky="e", padx=(8, 0))
            self.button(action_bar, "Detect Paths", self.detect_paths).pack(side=tk.LEFT)
            self.button(action_bar, "Install Livery", self.install_selected, accent=True).pack(side=tk.LEFT, padx=(8, 0))

            log_card, log_body = self.card(content, "Activity Log", "Detection, copy, layout rebuild and install results.")
            log_card.grid(row=3, column=0, columnspan=2, sticky="nsew")
            log_card.rowconfigure(1, weight=1)
            log_body.rowconfigure(0, weight=1)
            self.log_text = tk.Text(
                log_body,
                height=8,
                wrap="word",
                bg=self.color("log"),
                fg="#cfd7df",
                insertbackground="#ffffff",
                relief=tk.FLAT,
                bd=0,
                padx=10,
                pady=8,
                font=("Consolas", 9),
            )
            self.log_text.grid(row=0, column=0, sticky="nsew")
            log_scrollbar = ttk.Scrollbar(log_body, orient=tk.VERTICAL, command=self.log_text.yview, style="PMDG.Vertical.TScrollbar")
            log_scrollbar.grid(row=0, column=1, sticky="ns")
            self.log_text.configure(yscrollcommand=log_scrollbar.set)
            return page

        def _create_diagnostics_page(self):
            page = self.make_page("Diagnostics", "Validate paths, package writability, layout entries and livery inventory.")
            page.rowconfigure(1, weight=1)
            body = tk.Frame(page, bg=self.color("bg"))
            body.grid(row=1, column=0, sticky="nsew")
            body.columnconfigure(0, weight=1)
            body.rowconfigure(1, weight=1)

            tools_card, tools = self.card(body, "Tools", "Run checks before installing or rebuild layout.json after manual changes.")
            tools_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
            self.button(tools, "Run Diagnostics", self.run_diagnostics, accent=True).pack(side=tk.LEFT)
            self.button(tools, "Detect Paths", self.detect_paths).pack(side=tk.LEFT, padx=(8, 0))
            self.button(tools, "Rebuild Selected layout.json", self.rebuild_selected_layout).pack(side=tk.LEFT, padx=(8, 0))

            report_card, report = self.card(body, "Diagnostic Report", "Copy this output when troubleshooting missing liveries.")
            report_card.grid(row=1, column=0, sticky="nsew")
            report_card.rowconfigure(1, weight=1)
            report.rowconfigure(0, weight=1)
            self.diagnostics_text = tk.Text(
                report,
                bg=self.color("log"),
                fg="#cfd7df",
                relief=tk.FLAT,
                bd=0,
                padx=10,
                pady=8,
                wrap="word",
                font=("Consolas", 9),
            )
            self.diagnostics_text.grid(row=0, column=0, sticky="nsew")
            diagnostics_scrollbar = ttk.Scrollbar(report, orient=tk.VERTICAL, command=self.diagnostics_text.yview, style="PMDG.Vertical.TScrollbar")
            diagnostics_scrollbar.grid(row=0, column=1, sticky="ns")
            self.diagnostics_text.configure(yscrollcommand=diagnostics_scrollbar.set)
            return page

        def _create_settings_page(self):
            page = self.make_page("Settings", "Persist defaults and choose a comfortable window scale.")
            body = tk.Frame(page, bg=self.color("bg"))
            body.grid(row=1, column=0, sticky="nsew")
            body.columnconfigure(0, weight=1)

            settings_card, settings = self.card(body, "Install Behavior", "These settings are shared by the Liveries page.")
            settings_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
            self.checkbutton(settings, "Allow overwrite when matching files already exist", self.overwrite_var).grid(row=0, column=0, sticky="w", pady=(0, 5))
            self.checkbutton(settings, "Back up layout.json before rebuilding", self.backup_var).grid(row=1, column=0, sticky="w")
            self.checkbutton(settings, "Allow linked Community targets such as symlinks or junctions", self.allow_linked_targets_var).grid(row=2, column=0, sticky="w", pady=(5, 0))

            paths_card, paths = self.card(body, "Saved Paths", "Stored locally for the next launch.")
            paths_card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
            paths.columnconfigure(1, weight=1)
            self.label(paths, "Community", 8, self.color("muted"), "bold").grid(row=0, column=0, sticky="w", pady=(0, 5))
            self.entry(paths, self.community_var).grid(row=0, column=1, sticky="ew", pady=(0, 5), ipady=4)
            self.button(paths, "Browse", self.choose_community).grid(row=0, column=2, padx=(8, 0), pady=(0, 5))

            display_card, display = self.card(body, "Display", "Higher default resolution for a wider workspace.")
            display_card.grid(row=2, column=0, sticky="ew")
            self.button(display, "1180 x 720", lambda: self.geometry("1180x720")).pack(side=tk.LEFT)
            self.button(display, "1280 x 780", lambda: self.geometry("1280x780")).pack(side=tk.LEFT, padx=(8, 0))
            self.button(display, "1440 x 860", lambda: self.geometry("1440x860")).pack(side=tk.LEFT, padx=(8, 0))
            self.button(display, "Save Settings", self._save_settings, accent=True).pack(side=tk.RIGHT)
            return page

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
            self.status_var.set(f"{page_name} ready")

        def set_text(self, widget, text: str) -> None:
            widget.configure(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            widget.insert(tk.END, text)
            widget.configure(state=tk.NORMAL)

        def get_selected_package(self) -> Path | None:
            selected = self.package_var.get()
            if selected in self.package_paths:
                return self.package_paths[selected]
            if hasattr(self, "product_listbox"):
                selection = self.product_listbox.curselection()
                if selection:
                    index = selection[0]
                    if 0 <= index < len(self.detected_packages):
                        return self.detected_packages[index]
            if self.detected_packages:
                return self.detected_packages[0]
            return None

        def describe_package(self, package_root: Path) -> str:
            known_folder = known_airplane_folder_name(package_root)
            lines = [f"Package: {package_root}"]
            if known_folder:
                lines.append(f"Aircraft: {known_folder}")
            if not package_root.exists():
                livery_root = ensure_livery_package_root(package_root)
                lines.extend(
                    [
                        "Status: aircraft package not found in Community",
                        f"Livery target: {livery_root}",
                        "",
                    ]
                )
            else:
                lines.append("")
            manifest_path = package_root / "manifest.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                    lines.extend(
                        [
                            "Manifest:",
                            f"  title: {manifest.get('title', 'n/a')}",
                            f"  package_version: {manifest.get('package_version', 'n/a')}",
                            f"  total_package_size: {manifest.get('total_package_size', 'n/a')}",
                            "",
                        ]
                    )
                except (OSError, json.JSONDecodeError) as exc:
                    lines.extend(["Manifest:", f"  unreadable: {exc}", ""])
            else:
                lines.extend(["Manifest:", "  missing", ""])

            layout_path = package_root / "layout.json"
            if layout_path.exists():
                try:
                    layout = json.loads(layout_path.read_text(encoding="utf-8-sig"))
                    content = layout.get("content", [])
                    lines.extend(["Layout:", f"  entries: {len(content) if isinstance(content, list) else 'invalid'}", ""])
                except (OSError, json.JSONDecodeError) as exc:
                    lines.extend(["Layout:", f"  unreadable: {exc}", ""])
            else:
                lines.extend(["Layout:", "  missing", ""])

            airplanes = package_root / "SimObjects" / "Airplanes"
            aircraft_dirs = [p for p in airplanes.iterdir() if p.is_dir()] if airplanes.exists() else []
            lines.append("Aircraft folders:")
            if aircraft_dirs:
                for aircraft in sorted(aircraft_dirs, key=lambda p: p.name.lower()):
                    livery_root = aircraft / "liveries" / "pmdg"
                    livery_count = len([p for p in livery_root.iterdir() if p.is_dir()]) if livery_root.exists() else 0
                    lines.append(f"  {aircraft.name}: {livery_count} livery folder(s)")
            else:
                lines.append("  none found")
            lines.append("")

            livery_package = ensure_livery_package_root(package_root)
            lines.append("Companion livery package:")
            lines.append(f"  path: {livery_package}")
            lines.append(f"  status: {'present' if livery_package.exists() else 'not created'}")
            try:
                installed_liveries = list_installed_liveries(package_root)
                lines.append(f"  installed liveries: {len(installed_liveries)}")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"  livery scan failed: {exc}")
            lines.append("")

            try:
                total_files = sum(1 for _ in iter_layout_files(package_root))
                lines.append(f"Files included by layout builder: {total_files}")
            except OSError as exc:
                lines.append(f"File scan failed: {exc}")
            return "\n".join(lines)

        def update_product_views(self) -> None:
            if hasattr(self, "product_listbox"):
                self.product_listbox.delete(0, tk.END)
                for package in self.detected_packages:
                    self.product_listbox.insert(tk.END, package.name)
                if self.detected_packages:
                    self.product_listbox.selection_set(0)
                    self.product_listbox.activate(0)
                    self.set_text(self.product_detail_text, self.describe_package(self.detected_packages[0]))
                else:
                    self.set_text(
                        self.product_detail_text,
                        "No PMDG aircraft packages were detected.\n\nSelect the MSFS 2024 Community folder and refresh products.",
                    )

        def on_product_select(self, _event=None) -> None:
            selection = self.product_listbox.curselection()
            if not selection:
                return
            package = self.detected_packages[selection[0]]
            for label_text, package_path in self.package_paths.items():
                if package_path == package:
                    self.package_var.set(label_text)
                    break
            self.set_text(self.product_detail_text, self.describe_package(package))
            self.status_var.set(f"Selected {package.name}")

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
                f"Folder: {livery.path}",
                f"Thumbnail: {livery.thumbnail_path or 'not found'}",
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
            return "\n".join(lines)

        def clear_thumbnail(self, message: str) -> None:
            self.thumbnail_image = None
            self.thumbnail_label.configure(image="", text=message, fg=self.color("muted"))

        def load_thumbnail(self, path: Path):
            max_width = 520
            max_height = 240
            try:
                from PIL import Image, ImageTk  # type: ignore

                with Image.open(path) as image:
                    image.thumbnail((max_width, max_height))
                    return ImageTk.PhotoImage(image.copy()), None
            except ImportError:
                pass
            except Exception as exc:  # noqa: BLE001
                return None, str(exc)

            try:
                image = tk.PhotoImage(file=str(path))
                width = max(image.width(), 1)
                height = max(image.height(), 1)
                factor = max(
                    1,
                    (width + max_width - 1) // max_width,
                    (height + max_height - 1) // max_height,
                )
                if factor > 1:
                    image = image.subsample(factor, factor)
                return image, None
            except Exception as exc:  # noqa: BLE001
                converted_path, convert_error = self.convert_thumbnail_with_powershell(path, max_width, max_height)
                if not converted_path:
                    return None, str(exc) if not convert_error else convert_error
                try:
                    image = tk.PhotoImage(file=str(converted_path))
                    return image, None
                except Exception as converted_exc:  # noqa: BLE001
                    return None, str(converted_exc)
                finally:
                    try:
                        converted_path.unlink()
                    except OSError:
                        pass

        def convert_thumbnail_with_powershell(self, path: Path, max_width: int, max_height: int) -> tuple[Path | None, str | None]:
            if sys.platform != "win32":
                return None, None
            temp_dir = Path(os.environ.get("TEMP") or os.environ.get("TMP") or Path.cwd())
            output_path = temp_dir / f"pmdg_livery_thumb_{uuid.uuid4().hex}.png"
            script = r"""
& {
    param([string]$Source, [string]$Dest, [int]$MaxWidth, [int]$MaxHeight)
    Add-Type -AssemblyName System.Drawing
    $img = [System.Drawing.Image]::FromFile($Source)
    try {
        $scale = [Math]::Min($MaxWidth / $img.Width, $MaxHeight / $img.Height)
        if ($scale -gt 1) { $scale = 1 }
        $width = [Math]::Max(1, [int][Math]::Round($img.Width * $scale))
        $height = [Math]::Max(1, [int][Math]::Round($img.Height * $scale))
        $bmp = New-Object System.Drawing.Bitmap($width, $height)
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($bmp)
            try {
                $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $graphics.DrawImage($img, 0, 0, $width, $height)
            } finally {
                $graphics.Dispose()
            }
            $bmp.Save($Dest, [System.Drawing.Imaging.ImageFormat]::Png)
        } finally {
            $bmp.Dispose()
        }
    } finally {
        $img.Dispose()
    }
}
"""
            try:
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-Command", script, str(path), str(output_path), str(max_width), str(max_height)],
                    check=False,
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
            except OSError as exc:
                return None, str(exc)
            if result.returncode != 0 or not output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
                detail = (result.stderr or result.stdout or "PowerShell image conversion failed").strip()
                return None, detail
            return output_path, None

        def show_thumbnail(self, livery: InstalledLivery) -> None:
            if not livery.thumbnail_path:
                self.clear_thumbnail("No thumbnail found")
                return
            image, error = self.load_thumbnail(livery.thumbnail_path)
            if image is None:
                self.clear_thumbnail(f"Thumbnail preview failed\n{error}")
                return
            self.thumbnail_image = image
            self.thumbnail_label.configure(image=self.thumbnail_image, text="")

        def refresh_installed_liveries(self) -> None:
            if not hasattr(self, "installed_tree"):
                return
            package = self.get_selected_package()
            existing_items = self.installed_tree.get_children()
            if existing_items:
                self.installed_tree.delete(*existing_items)
            self.installed_livery_items.clear()
            self.installed_liveries = []
            if not package:
                self.set_text(self.installed_detail_text, "No PMDG product selected.")
                self.clear_thumbnail("No product selected")
                self.status_var.set("Select a PMDG product")
                return

            try:
                liveries = list_installed_liveries(package)
            except Exception as exc:  # noqa: BLE001
                self.set_text(self.installed_detail_text, f"Installed livery scan failed:\n{exc}")
                self.clear_thumbnail("Scan failed")
                self.status_var.set("Installed livery scan failed")
                self.log(f"ERROR: installed livery scan failed: {exc}")
                return

            self.installed_liveries = liveries
            for index, livery in enumerate(liveries):
                iid = str(index)
                modified = (
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(livery.modified_time))
                    if livery.modified_time
                    else "n/a"
                )
                self.installed_livery_items[iid] = livery
                self.installed_tree.insert(
                    "",
                    tk.END,
                    iid=iid,
                    values=(
                        livery.aircraft_name,
                        livery.metadata.get("title") or livery.name,
                        livery.file_count,
                        format_bytes(livery.total_size),
                        modified,
                    ),
                )

            if liveries:
                self.installed_tree.selection_set("0")
                self.installed_tree.focus("0")
                self.on_installed_livery_select()
            else:
                livery_root = ensure_livery_package_root(package)
                self.set_text(self.installed_detail_text, f"No installed liveries found.\n\nLivery package: {livery_root}")
                self.clear_thumbnail("No installed liveries")
            noun = "livery" if len(liveries) == 1 else "liveries"
            self.status_var.set(f"{len(liveries)} installed {noun}")
            self.log(f"Found {len(liveries)} installed {noun}.")

        def on_installed_livery_select(self, _event=None) -> None:
            livery = self.selected_installed_livery()
            if not livery:
                return
            self.set_text(self.installed_detail_text, self.installed_livery_details(livery))
            self.show_thumbnail(livery)
            self.status_var.set(f"Selected {livery.name}")

        def copy_installed_livery_path(self) -> None:
            livery = self.selected_installed_livery()
            if not livery:
                self.status_var.set("No installed livery selected")
                return
            self.clipboard_clear()
            self.clipboard_append(str(livery.path))
            self.status_var.set("Livery path copied")

        def uninstall_selected_livery(self) -> None:
            package = self.get_selected_package()
            livery = self.selected_installed_livery()
            if not package or not livery:
                messagebox.showerror("Missing livery", "Select an installed livery first.")
                return

            confirmed = messagebox.askyesno(
                "Uninstall livery",
                f"Remove this livery folder?\n\n{livery.aircraft_name}/{livery.name}\n\n{livery.path}",
            )
            if not confirmed:
                return

            try:
                self.status_var.set("Uninstalling livery")
                report = uninstall_livery(
                    package,
                    livery.path,
                    backup_layout=self.backup_var.get(),
                    allow_linked_targets=self.allow_linked_targets_var.get(),
                )
            except Exception as exc:  # noqa: BLE001
                self.status_var.set("Uninstall failed")
                self.log(f"ERROR: {exc}")
                messagebox.showerror("Uninstall failed", str(exc))
                return

            text = format_uninstall_report(report)
            self.log(text)
            self.refresh_installed_liveries()
            self.update_product_views()
            self.status_var.set("Uninstall complete")
            messagebox.showinfo("Uninstall complete", text)

        def writable_status(self, path: Path) -> str:
            if not path.exists():
                return "missing"
            if not path.is_dir():
                return "not a folder"
            probe = path / f".pmdg_write_test_{uuid.uuid4().hex}"
            try:
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                return "writable"
            except OSError as exc:
                return f"not writable ({exc})"

        def run_diagnostics(self) -> None:
            lines = [
                "PMDG Livery Installer MSFS2024 Diagnostics",
                f"Application: {sys.executable}",
                f"Community: {self.community_var.get().strip() or 'not set'}",
                "",
            ]

            community = Path(self.community_var.get().strip()) if self.community_var.get().strip() else None
            if community:
                lines.append(f"Community status: {self.writable_status(community)}")
            lines.append("")

            package = self.get_selected_package()
            if package:
                lines.append(self.describe_package(package))
            else:
                lines.append("No selected PMDG package.")

            self.set_text(self.diagnostics_text, "\n".join(lines))
            self.status_var.set("Diagnostics complete")

        def rebuild_selected_layout(self) -> None:
            package = self.get_selected_package()
            if not package:
                messagebox.showerror("Missing package", "Select a PMDG package first.")
                return
            try:
                layout_entries, manifest_updated, backup_path = rebuild_layout(package, backup=self.backup_var.get())
            except Exception as exc:  # noqa: BLE001
                self.status_var.set("Layout rebuild failed")
                messagebox.showerror("Layout rebuild failed", str(exc))
                return
            result = [
                f"Package: {package}",
                f"layout.json entries: {layout_entries}",
                f"manifest.json updated: {'yes' if manifest_updated else 'no'}",
            ]
            if backup_path:
                result.append(f"layout backup: {backup_path}")
            text = "\n".join(result)
            self.log(text)
            self.set_text(self.diagnostics_text, text)
            self.set_text(self.product_detail_text, self.describe_package(package))
            self.status_var.set("layout.json rebuilt")

        def log(self, message: str) -> None:
            self.log_text.insert(tk.END, message.rstrip() + "\n")
            self.log_text.see(tk.END)

        def detect_paths(self) -> None:
            self.status_var.set("Detecting simulator paths")
            detected = detect_msfs2024_paths()
            if detected.community_paths and not self.community_var.get():
                self.community_var.set(str(detected.community_paths[0]))
            self.log("Detected UserCfg.opt:")
            for path in detected.user_cfg_paths:
                self.log(f"  {path}")
            self.log("Detected Community folders:")
            for path in detected.community_paths:
                self.log(f"  {path}")
            self.refresh_packages()
            self.status_var.set("Path detection complete")

        def refresh_packages(self) -> None:
            self.status_var.set("Scanning PMDG products")
            self.package_paths.clear()
            community = self.community_var.get().strip()
            if not community:
                self.package_combo["values"] = []
                if hasattr(self, "installed_package_combo"):
                    self.installed_package_combo["values"] = []
                self.detected_packages = []
                self.update_product_views()
                self.refresh_installed_liveries()
                self.package_count_var.set("0 products detected")
                self.status_var.set("Select a Community folder")
                return
            packages = find_pmdg_product_roots(Path(community))
            self.detected_packages = packages
            values = []
            for package in packages:
                aircraft = known_airplane_folder_name(package)
                label_name = f"{aircraft} - {package.name}" if aircraft else package.name
                label = f"{label_name}    ({package})"
                self.package_paths[label] = package
                values.append(label)
            self.package_combo["values"] = values
            if hasattr(self, "installed_package_combo"):
                self.installed_package_combo["values"] = values
            if values and self.package_var.get() not in values:
                self.package_var.set(values[0])
            if not values:
                self.package_var.set("")
            noun = "product" if len(values) == 1 else "products"
            self.package_count_var.set(f"{len(values)} {noun} detected")
            self.update_product_views()
            self.refresh_installed_liveries()
            self.status_var.set("PMDG product scan complete")
            self.log(f"Found {len(values)} PMDG package(s).")

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
            selected = self.package_var.get()
            package_root = self.package_paths.get(selected)
            if not package_root:
                self.status_var.set("Missing PMDG product")
                messagebox.showerror("Missing package", "Select a PMDG package first.")
                return
            livery_path = self.livery_var.get().strip()
            if not livery_path:
                self.status_var.set("Missing livery source")
                messagebox.showerror("Missing livery", "Select a livery ZIP or folder first.")
                return

            try:
                self.status_var.set("Installing livery")
                report = install_livery(
                    Path(livery_path),
                    package_root,
                    overwrite=self.overwrite_var.get(),
                    backup_layout=self.backup_var.get(),
                    allow_linked_targets=self.allow_linked_targets_var.get(),
                )
            except Exception as exc:  # noqa: BLE001 - GUI should surface all failures.
                self.status_var.set("Install failed")
                self.log(f"ERROR: {exc}")
                messagebox.showerror("Install failed", str(exc))
                return

            text = format_report(report)
            self.log(text)
            self.refresh_installed_liveries()
            self.update_product_views()
            self.status_var.set("Install complete")
            messagebox.showinfo("Install complete", text)

    InstallerApp().mainloop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install PMDG MSFS 2024 liveries without PMDG OC3.",
    )
    parser.add_argument("--detect", action="store_true", help="Print detected MSFS 2024 paths.")
    parser.add_argument("--community", type=Path, help="MSFS 2024 Community folder.")
    parser.add_argument("--package", help="PMDG package folder name, e.g. pmdg-aircraft-738.")
    parser.add_argument("--package-root", type=Path, help="Full PMDG package folder path.")
    parser.add_argument("--livery", type=Path, help="Livery ZIP or extracted livery folder.")
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
    args = parser.parse_args(argv)

    if len(sys.argv if argv is None else argv) == 1 or args.gui:
        launch_gui()
        return 0

    if args.detect:
        print_detected_paths()
        return 0

    if args.list_liveries:
        try:
            package_root = resolve_package_from_args(args)
            liveries = list_installed_liveries(package_root)
        except InstallerError as exc:
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
        except InstallerError as exc:
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
    except InstallerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

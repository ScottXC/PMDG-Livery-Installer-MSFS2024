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
            "bg": "#0b0f14",
            "top": "#080b0f",
            "sidebar": "#0d1218",
            "panel": "#121922",
            "panel_alt": "#151e28",
            "field": "#0f151d",
            "log": "#080d12",
            "line": "#2b3541",
            "muted": "#9aa7b3",
            "text": "#e8edf2",
            "red": "#b51f2c",
            "red_hover": "#d22937",
            "amber": "#f2b94b",
            "green": "#47c283",
        }

        def __init__(self) -> None:
            super().__init__()
            self.title("PMDG Livery Installer MSFS2024")
            self.geometry("1440x900")
            self.minsize(1280, 760)
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
            self.option_add("*TCombobox*Listbox.background", "#111821")
            self.option_add("*TCombobox*Listbox.foreground", "#e8edf2")
            self.option_add("*TCombobox*Listbox.selectBackground", "#b51f2c")
            self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

            style = ttk.Style(self)
            style.theme_use("clam")
            style.configure(
                "PMDG.TCombobox",
                fieldbackground="#111821",
                background="#111821",
                foreground="#e8edf2",
                bordercolor="#2b3541",
                lightcolor="#2b3541",
                darkcolor="#2b3541",
                arrowcolor="#d7dde4",
                padding=8,
            )

            topbar = tk.Frame(self, bg=self.color("top"), height=104)
            topbar.pack(fill=tk.X)
            topbar.pack_propagate(False)

            brand = tk.Frame(topbar, bg=self.color("top"))
            brand.pack(side=tk.LEFT, padx=22, pady=18)
            tk.Label(
                brand,
                text="PMDG",
                bg=self.color("red"),
                fg="#ffffff",
                font=("Segoe UI", 13, "bold"),
                width=7,
                pady=10,
            ).pack(side=tk.LEFT)
            title_box = tk.Frame(brand, bg=self.color("top"))
            title_box.pack(side=tk.LEFT, padx=(14, 0))
            tk.Label(
                title_box,
                text="PMDG Livery Installer MSFS2024",
                bg=self.color("top"),
                fg="#ffffff",
                font=("Segoe UI", 15, "bold"),
                anchor="w",
            ).pack(anchor="w")
            tk.Label(
                title_box,
                text="Manual livery management for PMDG aircraft packages",
                bg=self.color("top"),
                fg=self.color("muted"),
                font=("Segoe UI", 9),
                anchor="w",
            ).pack(anchor="w", pady=(4, 0))
            tk.Label(
                topbar,
                textvariable=self.status_var,
                bg=self.color("top"),
                fg=self.color("muted"),
                font=("Segoe UI", 10),
                anchor="e",
            ).pack(side=tk.RIGHT, padx=24)
            tk.Frame(self, bg=self.color("red"), height=3).pack(fill=tk.X)

            body = tk.Frame(self, bg=self.color("bg"))
            body.pack(fill=tk.BOTH, expand=True)

            sidebar = tk.Frame(body, bg=self.color("sidebar"), width=210)
            sidebar.pack(side=tk.LEFT, fill=tk.Y)
            sidebar.pack_propagate(False)
            tk.Label(
                sidebar,
                text="OPERATIONS",
                bg=self.color("sidebar"),
                fg=self.color("muted"),
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            ).pack(fill=tk.X, padx=18, pady=(24, 8))

            for page_name in ("Products", "Liveries", "Diagnostics", "Settings"):
                nav = tk.Button(
                    sidebar,
                    text=page_name,
                    command=lambda name=page_name: self.show_page(name),
                    bg=self.color("sidebar"),
                    activebackground=self.color("red_hover"),
                    fg="#c4ccd5",
                    activeforeground="#ffffff",
                    relief=tk.FLAT,
                    bd=0,
                    cursor="hand2",
                    font=("Segoe UI", 10),
                    anchor="w",
                    padx=18,
                    pady=12,
                )
                nav.pack(fill=tk.X, pady=(2, 0))
                self.nav_buttons[page_name] = nav
            tk.Label(
                sidebar,
                text="MSFS 2024\nCommunity Package Mode",
                bg=self.color("sidebar"),
                fg=self.color("muted"),
                justify=tk.LEFT,
                font=("Segoe UI", 9),
                anchor="sw",
            ).pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=20)

            self.page_container = tk.Frame(body, bg=self.color("bg"))
            self.page_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=18, pady=18)
            self.page_container.rowconfigure(0, weight=1)
            self.page_container.columnconfigure(0, weight=1)

            for page_name, factory in (
                ("Products", self._create_products_page),
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
            bg = self.color("red") if accent else "#1c2632"
            active = self.color("red_hover") if accent else "#263241"
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
                padx=16,
                pady=9,
                cursor="hand2",
                font=("Segoe UI", 10, "bold" if accent else "normal"),
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
                highlightbackground=self.color("line"),
                highlightcolor=self.color("red"),
                bd=0,
                font=("Segoe UI", 10),
            )

        def card(self, parent, title, subtitle=None):
            frame = tk.Frame(parent, bg=self.color("panel"), highlightthickness=1, highlightbackground="#202a35")
            frame.columnconfigure(0, weight=1)
            header = tk.Frame(frame, bg=self.color("panel"))
            header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 10))
            self.label(header, title, 12, self.color("text"), "bold").pack(anchor="w")
            if subtitle:
                self.label(header, subtitle, 9, self.color("muted")).pack(anchor="w", pady=(2, 0))
            content = tk.Frame(frame, bg=self.color("panel"))
            content.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))
            content.columnconfigure(0, weight=1)
            return frame, content

        def make_page(self, headline, subhead):
            page = tk.Frame(self.page_container, bg=self.color("bg"))
            page.rowconfigure(1, weight=1)
            page.columnconfigure(0, weight=1)
            header = tk.Frame(page, bg=self.color("panel_alt"), highlightthickness=1, highlightbackground=self.color("line"))
            header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
            header.columnconfigure(0, weight=1)
            self.label(header, headline.upper(), 9, self.color("amber"), "bold").grid(row=0, column=0, sticky="w", padx=16, pady=(12, 2))
            self.label(header, subhead, 10, self.color("text")).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 12))
            return page

        def _create_products_page(self):
            page = self.make_page("Products", "Scan installed PMDG packages and inspect package health.")
            content = tk.Frame(page, bg=self.color("bg"))
            content.grid(row=1, column=0, sticky="nsew")
            content.rowconfigure(1, weight=1)
            content.columnconfigure(0, weight=1)
            content.columnconfigure(1, weight=2)

            summary_card, summary = self.card(content, "Product Library", "Community packages that look like PMDG aircraft products.")
            summary_card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
            summary.columnconfigure(0, weight=1)
            tk.Label(
                summary,
                textvariable=self.package_count_var,
                bg=self.color("panel"),
                fg=self.color("amber"),
                font=("Segoe UI", 24, "bold"),
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
                selectbackground=self.color("red"),
                selectforeground="#ffffff",
                relief=tk.FLAT,
                bd=0,
                highlightthickness=1,
                highlightbackground=self.color("line"),
                font=("Segoe UI", 10),
                activestyle="none",
            )
            self.product_listbox.grid(row=0, column=0, sticky="nsew")
            product_scrollbar = ttk.Scrollbar(list_body, orient=tk.VERTICAL, command=self.product_listbox.yview)
            product_scrollbar.grid(row=0, column=1, sticky="ns")
            self.product_listbox.configure(yscrollcommand=product_scrollbar.set)
            self.product_listbox.bind("<<ListboxSelect>>", self.on_product_select)

            detail_card, detail_body = self.card(content, "Product Details", "Manifest, layout, aircraft folders and livery inventory.")
            detail_card.grid(row=1, column=1, sticky="nsew", padx=(7, 0))
            detail_card.rowconfigure(1, weight=1)
            detail_body.rowconfigure(0, weight=1)
            self.product_detail_text = tk.Text(
                detail_body,
                bg=self.color("log"),
                fg="#cfd7df",
                relief=tk.FLAT,
                bd=0,
                padx=14,
                pady=12,
                wrap="word",
                font=("Consolas", 10),
            )
            self.product_detail_text.grid(row=0, column=0, sticky="nsew")
            detail_scrollbar = ttk.Scrollbar(detail_body, orient=tk.VERTICAL, command=self.product_detail_text.yview)
            detail_scrollbar.grid(row=0, column=1, sticky="ns")
            self.product_detail_text.configure(yscrollcommand=detail_scrollbar.set)
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
            paths_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7), pady=(0, 14))
            paths.columnconfigure(1, weight=1)
            self.label(paths, "Community folder", 9, self.color("muted"), "bold").grid(row=0, column=0, sticky="w", pady=(4, 6))
            self.entry(paths, self.community_var).grid(row=0, column=1, sticky="ew", pady=(4, 6), ipady=7)
            self.button(paths, "Browse", self.choose_community).grid(row=0, column=2, padx=(12, 0), pady=(4, 6))

            package_card, package = self.card(content, "Aircraft Package", "Choose the PMDG product to receive the livery.")
            package_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0), pady=(0, 14))
            package.columnconfigure(0, weight=1)
            tk.Label(
                package,
                textvariable=self.package_count_var,
                bg=self.color("panel"),
                fg=self.color("amber"),
                font=("Segoe UI", 20, "bold"),
                anchor="w",
            ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
            self.package_combo = ttk.Combobox(package, textvariable=self.package_var, state="readonly", style="PMDG.TCombobox")
            self.package_combo.grid(row=1, column=0, sticky="ew", padx=(0, 8), ipady=4)
            self.button(package, "Refresh", self.refresh_packages).grid(row=1, column=1)

            install_card, install = self.card(content, "Livery Package", "Select a ZIP or extracted livery folder.")
            install_card.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 14))
            install.columnconfigure(1, weight=1)
            self.label(install, "Source", 9, self.color("muted"), "bold").grid(row=0, column=0, sticky="w", pady=(0, 12))
            self.entry(install, self.livery_var).grid(row=0, column=1, sticky="ew", pady=(0, 12), ipady=7)
            browse_menu = tk.Frame(install, bg=self.color("panel"))
            browse_menu.grid(row=0, column=2, sticky="e", padx=(12, 0), pady=(0, 12))
            self.button(browse_menu, "ZIP", self.choose_zip).pack(side=tk.LEFT)
            self.button(browse_menu, "Folder", self.choose_livery_folder).pack(side=tk.LEFT, padx=(8, 0))
            options = tk.Frame(install, bg=self.color("panel"))
            options.grid(row=1, column=1, sticky="w")
            self.checkbutton(options, "Allow overwrite", self.overwrite_var).pack(side=tk.LEFT)
            self.checkbutton(options, "Backup layout.json", self.backup_var).pack(side=tk.LEFT, padx=(18, 0))
            self.checkbutton(options, "Allow linked targets", self.allow_linked_targets_var).pack(side=tk.LEFT, padx=(18, 0))
            action_bar = tk.Frame(install, bg=self.color("panel"))
            action_bar.grid(row=1, column=2, sticky="e", padx=(12, 0))
            self.button(action_bar, "Detect Paths", self.detect_paths).pack(side=tk.LEFT)
            self.button(action_bar, "Install Livery", self.install_selected, accent=True).pack(side=tk.LEFT, padx=(10, 0))

            log_card, log_body = self.card(content, "Activity Log", "Detection, copy, layout rebuild and install results.")
            log_card.grid(row=3, column=0, columnspan=2, sticky="nsew")
            log_card.rowconfigure(1, weight=1)
            log_body.rowconfigure(0, weight=1)
            self.log_text = tk.Text(
                log_body,
                height=11,
                wrap="word",
                bg=self.color("log"),
                fg="#cfd7df",
                insertbackground="#ffffff",
                relief=tk.FLAT,
                bd=0,
                padx=14,
                pady=12,
                font=("Consolas", 9),
            )
            self.log_text.grid(row=0, column=0, sticky="nsew")
            log_scrollbar = ttk.Scrollbar(log_body, orient=tk.VERTICAL, command=self.log_text.yview)
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
            tools_card.grid(row=0, column=0, sticky="ew", pady=(0, 14))
            self.button(tools, "Run Diagnostics", self.run_diagnostics, accent=True).pack(side=tk.LEFT)
            self.button(tools, "Detect Paths", self.detect_paths).pack(side=tk.LEFT, padx=(10, 0))
            self.button(tools, "Rebuild Selected layout.json", self.rebuild_selected_layout).pack(side=tk.LEFT, padx=(10, 0))

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
                padx=14,
                pady=12,
                wrap="word",
                font=("Consolas", 10),
            )
            self.diagnostics_text.grid(row=0, column=0, sticky="nsew")
            diagnostics_scrollbar = ttk.Scrollbar(report, orient=tk.VERTICAL, command=self.diagnostics_text.yview)
            diagnostics_scrollbar.grid(row=0, column=1, sticky="ns")
            self.diagnostics_text.configure(yscrollcommand=diagnostics_scrollbar.set)
            return page

        def _create_settings_page(self):
            page = self.make_page("Settings", "Persist defaults and choose a comfortable window scale.")
            body = tk.Frame(page, bg=self.color("bg"))
            body.grid(row=1, column=0, sticky="nsew")
            body.columnconfigure(0, weight=1)

            settings_card, settings = self.card(body, "Install Behavior", "These settings are shared by the Liveries page.")
            settings_card.grid(row=0, column=0, sticky="ew", pady=(0, 14))
            self.checkbutton(settings, "Allow overwrite when matching files already exist", self.overwrite_var).grid(row=0, column=0, sticky="w", pady=(0, 8))
            self.checkbutton(settings, "Back up layout.json before rebuilding", self.backup_var).grid(row=1, column=0, sticky="w")
            self.checkbutton(settings, "Allow linked Community targets such as symlinks or junctions", self.allow_linked_targets_var).grid(row=2, column=0, sticky="w", pady=(8, 0))

            paths_card, paths = self.card(body, "Saved Paths", "Stored locally for the next launch.")
            paths_card.grid(row=1, column=0, sticky="ew", pady=(0, 14))
            paths.columnconfigure(1, weight=1)
            self.label(paths, "Community", 9, self.color("muted"), "bold").grid(row=0, column=0, sticky="w", pady=(0, 8))
            self.entry(paths, self.community_var).grid(row=0, column=1, sticky="ew", pady=(0, 8), ipady=7)
            self.button(paths, "Browse", self.choose_community).grid(row=0, column=2, padx=(12, 0), pady=(0, 8))

            display_card, display = self.card(body, "Display", "Higher default resolution for a wider workspace.")
            display_card.grid(row=2, column=0, sticky="ew")
            self.button(display, "1280 x 780", lambda: self.geometry("1280x780")).pack(side=tk.LEFT)
            self.button(display, "1440 x 900", lambda: self.geometry("1440x900")).pack(side=tk.LEFT, padx=(10, 0))
            self.button(display, "1600 x 960", lambda: self.geometry("1600x960")).pack(side=tk.LEFT, padx=(10, 0))
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
                activeforeground=self.color("text"),
                relief=tk.FLAT,
                font=("Segoe UI", 10),
            )

        def show_page(self, page_name: str) -> None:
            self.pages[page_name].tkraise()
            for name, button in self.nav_buttons.items():
                active = name == page_name
                button.configure(
                    bg=self.color("red") if active else self.color("sidebar"),
                    fg="#ffffff" if active else "#c4ccd5",
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
                self.detected_packages = []
                self.update_product_views()
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
            if values and self.package_var.get() not in values:
                self.package_var.set(values[0])
            if not values:
                self.package_var.set("")
            noun = "product" if len(values) == 1 else "products"
            self.package_count_var.set(f"{len(values)} {noun} detected")
            self.update_product_views()
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

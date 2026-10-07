"""Build distribution ZIPs and upload copy from already-built release binaries."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sys
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_version import VERSION

APP = "PMDG Livery Installer MSFS2024"
RELEASE = ROOT / "release"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_zip(destination: Path, binary: Path, readme: str, changelog: str) -> None:
    contents = {
        "README.txt": readme.encode("utf-8"),
        "CHANGELOG.txt": changelog.encode("utf-8"),
        "DOCUMENTATION.md": (ROOT / "README.md").read_bytes(),
    }
    hashes = [f"{digest(binary)}  {binary.name}"]
    hashes.extend(f"{hashlib.sha256(data).hexdigest()}  {name}" for name, data in contents.items())
    contents["SHA256SUMS.txt"] = ("\n".join(hashes) + "\n").encode("utf-8")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        entry = zipfile.ZipInfo(binary.name, date_time=(2000, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        entry.create_system = 0
        entry.external_attr = 0x20
        archive.writestr(entry, binary.read_bytes())
        for name, data in contents.items():
            entry = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 0
            entry.external_attr = 0x20
            archive.writestr(entry, data)
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None, "Archive CRC validation failed"
        for line in hashes:
            expected, name = line.split("  ", 1)
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected, name
    print(f"Verified: {destination.name} ({destination.stat().st_size:,} bytes)")


def main() -> None:
    setup = RELEASE / f"{APP} Setup v{VERSION}.exe"
    portable = ROOT / "dist" / f"{APP}.exe"
    for binary in (setup, portable):
        if not binary.is_file():
            raise SystemExit(f"Build the installer first: missing {binary}")
        with binary.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise SystemExit(f"Not a Windows executable: {binary}")
    changelog = (ROOT / "docs" / "releases" / f"v{VERSION}.md").read_text(encoding="utf-8")
    common = f"""{APP} v{VERSION}

QUICK START
Open Install liveries, choose the Community folder used by MSFS 2024, and select
your PMDG aircraft. Choose a compatible ZIP or extracted folder, click Review &
Install, check the destination/conflicts, then confirm. Check the livery in MSFS.
Use My liveries to browse, search, export, uninstall, or restore a package backup.
Do not put this utility into the Community folder.

Backups: Community/.pmdg-livery-backups/<package>/<timestamp>/
For explicitly allowed linked targets, backups are beside the real package.
Restore backup replaces the entire companion livery package. See DOCUMENTATION.md.

Windows executable includes Python, Pillow, and MSFSLayoutGenerator.
No separate Python installation is required. Aircraft and liveries are not included.
Use compatible PMDG MSFS 2024 sources. PTP import and MSFS 2020 conversion are not
supported. This release has not been tested in a real simulator session.

SOURCE AND SUPPORT
https://github.com/ScottXC/PMDG-Livery-Installer-MSFS2024
https://github.com/ScottXC/PMDG-Livery-Installer-MSFS2024/issues

中文快速说明
在 Install liveries 页面选择 MSFS 2024 的 Community 文件夹及目标 PMDG 机型，
选择兼容的 ZIP 或解压文件夹，点击 Review & Install 核对后安装。
My liveries 提供浏览、搜索、导出、卸载及完整包备份恢复。不要把工具放进 Community。
本版未在真实模拟器会话中验证，不支持 PTP 导入及 MSFS 2020 转换。
"""
    upload = RELEASE / f"PMDG-Livery-Installer-MSFS2024-v{VERSION}-Flightsim.to.zip"
    portable_zip = RELEASE / f"PMDG-Livery-Installer-MSFS2024-v{VERSION}-Portable.zip"
    build_zip(upload, setup, f"Extract all files, then run {setup.name}.\n\n" + common, changelog)
    build_zip(portable_zip, portable, f"Extract all files, then run {portable.name}.\n\n" + common, changelog)
    materials = RELEASE / f"flightsim-to-v{VERSION}"
    materials.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "docs" / "releases" / f"flightsim-to-v{VERSION}.md", materials / "LISTING.md")
    shutil.copyfile(ROOT / "docs" / "releases" / f"v{VERSION}.md", materials / "CHANGELOG.md")
    shutil.copyfile(ROOT / "assets" / "pmdg_livery_installer_icon.png", materials / "app-icon.png")
    (materials / "UPLOAD.txt").write_text(
        f"Download file to upload: ../{upload.name}\n"
        "Listing text: LISTING.md\nRelease notes: CHANGELOG.md\n"
        "app-icon.png is the application icon, not a screenshot.\n"
        "未包含新的已核验界面截图。上传说明请见 LISTING.md。\n", encoding="utf-8")
    checksums = RELEASE / f"SHA256SUMS-v{VERSION}.txt"
    # GitHub normalizes spaces to dots when naming release assets.
    public_setup = RELEASE / setup.name.replace(" ", ".")
    shutil.copyfile(setup, public_setup)
    checksums.write_text("\n".join(f"{digest(path)}  {path.name}" for path in (public_setup, upload, portable_zip)) + "\n", encoding="utf-8")
    print(f"Upload copy: {materials}\nChecksums: {checksums}")
    subprocess.run([sys.executable, str(ROOT / "tools" / "audit_release_privacy.py")], check=True)


if __name__ == "__main__":
    main()

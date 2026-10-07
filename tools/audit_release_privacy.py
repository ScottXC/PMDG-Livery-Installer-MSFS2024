"""Audit current release files without copying local identifiers into the report.

Examines tracked source, ZIP members, Inno 7 metadata/payloads, PyInstaller's
compressed entries, nested Python code objects and PNG metadata. Known local
identifiers come from the running build environment; public project attribution
is not a machine identifier. Unsupported installer formats fail closed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import ipaddress
import json
import marshal
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import types
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT / ".build_tools"))
from app_version import VERSION
from tools.inno7_payloads import inspect as inspect_inno


def local_identifiers():
    result = {"workspace": str(ROOT), "workspace_parent": str(ROOT.parent),
              "python_install": str(Path(sys.executable).parent)}
    for key in ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "CODEX_HOME", "TEMP", "TMP", "USERNAME", "COMPUTERNAME"):
        value = os.environ.get(key)
        if value and len(value) >= 4:
            result[key.lower()] = value
    for index, address in enumerate(sorted({entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None)})):
        parsed = ipaddress.ip_address(address)
        if parsed.is_private and not parsed.is_loopback:
            result[f"local_ip_{index}"] = address
    node = uuid.getnode()
    if not (node >> 40) & 1:
        mac = ":".join(f"{(node >> shift) & 255:02x}" for shift in range(40, -1, -8))
        result["mac_colons"], result["mac_dashes"] = mac, mac.replace(":", "-")
    return result


class Auditor:
    def __init__(self, identifiers, public_urls=()):
        self.patterns = []
        self.public_urls = [url.lower().encode(encoding) for url in public_urls for encoding in ("utf-8", "utf-16le")]
        for label, value in identifiers.items():
            variants = {value, value.replace("\\", "/"), value.replace("\\", "\\\\")}
            for variant in variants:
                for encoding in ("utf-8", "utf-16le"):
                    word = b"[a-z0-9_]" if encoding == "utf-8" else b"[a-z0-9_]\x00"
                    needle = variant.lower().encode(encoding)
                    pattern = re.compile(b"(?<!" + word + b")" + re.escape(needle) + b"(?!" + word + b")")
                    self.patterns.append((label, needle, pattern))
        self.findings, self.files, self.counts, self.seen = [], [], Counter(), set()
        self.inno_payload_hashes = set()
        self.temp = ROOT / ".tmp" / "privacy-audit"
        self.temp.mkdir(parents=True, exist_ok=True)

    def finding(self, origin, kind):
        item = {"file": origin, "issue": kind}
        if item not in self.findings:
            self.findings.append(item)

    def strings(self, data, origin):
        lower = data.lower()
        for public_url in self.public_urls:
            lower = lower.replace(public_url, b"<public-project-url>")
        for label, needle, pattern in self.patterns:
            if needle in lower and pattern.search(lower):
                self.finding(origin, f"local identifier: {label}")
        if re.search(rb"gh[pousr]_[A-Za-z0-9]{30,}|sk-proj-[A-Za-z0-9_-]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", data):
            self.finding(origin, "possible private credential")

    def code(self, value, origin):
        if isinstance(value, types.CodeType):
            self.counts["python_code_objects"] += 1
            if re.match(r"^[A-Za-z]:[/\\]|^/", value.co_filename):
                self.finding(origin, "absolute Python code filename")
            self.strings(value.co_filename.encode("utf-8"), origin)
            for constant in value.co_consts:
                self.code(constant, origin)
        elif isinstance(value, str):
            self.strings(value.encode("utf-8", errors="replace"), origin)
        elif isinstance(value, bytes):
            self.strings(value, origin)
        elif isinstance(value, (tuple, frozenset)):
            for item in value:
                self.code(item, origin)

    def blob(self, data, origin):
        digest = hashlib.sha256(data).hexdigest()
        if digest in self.seen:
            return
        self.seen.add(digest)
        self.counts["unique_blobs"] += 1
        self.strings(data, origin)
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            self.counts["png_images"] += 1
            offset = 8
            while offset < len(data):
                size = struct.unpack_from(">I", data, offset)[0]
                kind = data[offset + 4:offset + 8]
                if kind in (b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"):
                    self.finding(origin, "PNG contains text, EXIF, or time metadata")
                offset += size + 12
            if offset != len(data):
                self.finding(origin, "PNG has trailing or malformed bytes")
        elif data.startswith(b"PK\x03\x04"):
            self.counts["zip_archives"] += 1
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if archive.comment:
                    self.finding(origin, "ZIP archive comment present")
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    self.counts["zip_members"] += 1
                    name = info.filename.replace("\\", "/")
                    if name.startswith("/") or re.match("^[A-Za-z]:", name) or ".." in name.split("/"):
                        self.finding(origin, "unsafe or absolute ZIP member name")
                    if Path(name).name.lower() in {"settings.json", ".env", "auth.json", "id_rsa", "id_ed25519", "diagnostics.txt"}:
                        self.finding(origin, "unexpected personal/configuration file")
                    if info.extra or info.comment:
                        self.finding(origin, "ZIP extended metadata or member comment present")
                    self.strings(info.filename.encode("utf-8"), origin)
                    child = archive.read(info)
                    if name.endswith(".pyc"):
                        self.code(marshal.loads(child[16:]), origin + "!" + name)
                    self.blob(child, origin + "!" + name)
        elif data.startswith(b"MZ") and b"rDlPtS\xcd\xe6\xd7{\x0b*" in data:
            header, runtime, payloads = inspect_inno(data)
            self.counts["inno_installers"] += 1
            self.counts["inno_verified_payloads"] += len(payloads)
            self.blob(header, origin + "!metadata")
            self.blob(runtime, origin + "!runtime")
            for index, payload in enumerate(payloads):
                self.inno_payload_hashes.add(hashlib.sha256(payload).hexdigest())
                self.blob(payload, origin + f"!payload-{index}")
        elif data.startswith(b"MZ") and b"MEI\014\013\012\013\016" in data:
            from PyInstaller.archive.readers import CArchiveReader
            filename = self.temp / f"{digest}.exe"
            if not filename.exists():
                filename.write_bytes(data)
            archive = CArchiveReader(str(filename))
            self.counts["pyinstaller_executables"] += 1
            for name, entry in archive.toc.items():
                self.strings(name.encode("utf-8"), origin)
                if entry[-1] == "z":
                    pyz = archive.open_embedded_archive(name)
                    for module in pyz.toc:
                        self.code(pyz.extract(module), origin + "!" + module)
                        self.counts["pyz_modules"] += 1
                else:
                    child = archive.extract(name)
                    if isinstance(child, bytes):
                        if entry[-1] == "s":
                            self.code(marshal.loads(child), origin + "!" + name)
                        self.blob(child, origin + "!" + name)

    def file(self, path):
        origin = path.relative_to(ROOT).as_posix()
        data = path.read_bytes()
        self.files.append({"file": origin, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        try:
            self.blob(data, origin)
        except Exception as error:
            self.finding(origin, "inspection failed: " + type(error).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "release" / f"PRIVACY-AUDIT-v{VERSION}.json")
    args = parser.parse_args()
    remote = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=ROOT).decode().strip().removesuffix(".git")
    auditor = Auditor(local_identifiers(), [remote] if remote.startswith("https://github.com/") else [])
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    paths = {ROOT / name for name in tracked if name}
    # Include new audit/build source in an audit run before it has been committed.
    paths.update((ROOT / "tools").glob("*.py"))
    paths.update(ROOT.glob("test_*.py"))
    paths.update((ROOT / "release").glob(f"*v{VERSION}*.exe"))
    paths.update((ROOT / "release").glob(f"*v{VERSION}*.zip"))
    paths.add(ROOT / "release" / f"SHA256SUMS-v{VERSION}.txt")
    paths.update(path for path in (ROOT / "release" / f"flightsim-to-v{VERSION}").rglob("*") if path.is_file())
    for path in sorted(paths):
        auditor.file(path)
    for path in (ROOT / "dist" / "PMDG Livery Installer MSFS2024.exe", ROOT / "README.md",
                 ROOT / "assets" / "pmdg_livery_installer_icon.ico"):
        if hashlib.sha256(path.read_bytes()).hexdigest() not in auditor.inno_payload_hashes:
            auditor.finding(path.relative_to(ROOT).as_posix(), "current file not found in verified installer payloads")
    result = {"version": VERSION, "status": "PASS" if not auditor.findings else "FAIL",
              "scope": "Current working tree and v" + VERSION + " deliverables; Git history and earlier releases are excluded.",
              "checks": dict(auditor.counts), "findings": auditor.findings, "files": auditor.files,
              "public_identity": "Only the public GitHub project/support URL is exempt from machine-identifier matching."}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result["status"], dict(auditor.counts))
    for finding in auditor.findings:
        print(finding["file"], "-", finding["issue"])
    print("Report:", args.report.relative_to(ROOT).as_posix())
    return bool(auditor.findings)


if __name__ == "__main__":
    raise SystemExit(main())

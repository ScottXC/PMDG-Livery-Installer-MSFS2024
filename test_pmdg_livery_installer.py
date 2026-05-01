import json
import shutil
import unittest
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from pmdg_livery_installer import install_livery


def make_package(root: Path) -> Path:
    package = root / "Community" / "pmdg-aircraft-738"
    airplane = package / "SimObjects" / "Airplanes" / "PMDG 737-800"
    airplane.mkdir(parents=True)
    (package / "layout.json").write_text('{"content":[]}\n', encoding="utf-8")
    (package / "manifest.json").write_text(
        json.dumps({"total_package_size": "0"}, indent=2),
        encoding="utf-8",
    )
    (airplane / "aircraft.cfg").write_text("[VERSION]\nmajor=1\nminor=0\n", encoding="utf-8")
    return package


@contextmanager
def workspace_root():
    root = Path.cwd() / f".test_workspace_{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class InstallerTests(unittest.TestCase):
    def test_installs_zip_with_simobjects_and_rebuilds_layout(self) -> None:
        with workspace_root() as root:
            package = make_package(root)
            zip_path = root / "livery.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr(
                    "release/SimObjects/Airplanes/PMDG 737-800/liveries/pmdg/Test Livery/livery.cfg",
                    "[VERSION]\nmajor=1\nminor=0\n",
                )
                archive.writestr("release/MSFSLayoutGenerator.exe", "not copied")

            report = install_livery(zip_path, package)

            installed = (
                package
                / "SimObjects"
                / "Airplanes"
                / "PMDG 737-800"
                / "liveries"
                / "pmdg"
                / "Test Livery"
                / "livery.cfg"
            )
            self.assertTrue(installed.exists())
            self.assertFalse((package / "MSFSLayoutGenerator.exe").exists())
            self.assertGreater(report.layout_entries, 0)

            layout = json.loads((package / "layout.json").read_text(encoding="utf-8"))
            paths = {entry["path"] for entry in layout["content"]}
            self.assertIn(
                "SimObjects/Airplanes/PMDG 737-800/liveries/pmdg/Test Livery/livery.cfg",
                paths,
            )

            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotEqual(manifest["total_package_size"], "0")

    def test_installs_zip_based_ptp(self) -> None:
        with workspace_root() as root:
            package = make_package(root)
            ptp_path = root / "livery.ptp"
            with zipfile.ZipFile(ptp_path, "w") as archive:
                archive.writestr(
                    "SimObjects/Airplanes/PMDG 737-800/liveries/pmdg/PTP Livery/livery.cfg",
                    "[VERSION]\nmajor=1\nminor=0\n",
                )

            install_livery(ptp_path, package)

            self.assertTrue(
                (
                    package
                    / "SimObjects"
                    / "Airplanes"
                    / "PMDG 737-800"
                    / "liveries"
                    / "pmdg"
                    / "PTP Livery"
                    / "livery.cfg"
                ).exists()
            )

    def test_installs_zip_containing_ptp(self) -> None:
        with workspace_root() as root:
            package = make_package(root)
            ptp_path = root / "inner.ptp"
            with zipfile.ZipFile(ptp_path, "w") as archive:
                archive.writestr(
                    "SimObjects/Airplanes/PMDG 737-800/liveries/pmdg/Nested PTP/livery.cfg",
                    "[VERSION]\nmajor=1\nminor=0\n",
                )

            outer_zip = root / "download.zip"
            with zipfile.ZipFile(outer_zip, "w") as archive:
                archive.write(ptp_path, "downloaded/inner.ptp")

            install_livery(outer_zip, package)

            self.assertTrue(
                (
                    package
                    / "SimObjects"
                    / "Airplanes"
                    / "PMDG 737-800"
                    / "liveries"
                    / "pmdg"
                    / "Nested PTP"
                    / "livery.cfg"
                ).exists()
            )

    def test_installs_direct_livery_folder(self) -> None:
        with workspace_root() as root:
            package = make_package(root)
            livery = root / "My Direct Livery"
            (livery / "texture.TEST").mkdir(parents=True)
            (livery / "livery.cfg").write_text("[VERSION]\nmajor=1\nminor=0\n", encoding="utf-8")
            (livery / "texture.TEST" / "texture.cfg").write_text("[fltsim]\n", encoding="utf-8")

            install_livery(livery, package)

            self.assertTrue(
                (
                    package
                    / "SimObjects"
                    / "Airplanes"
                    / "PMDG 737-800"
                    / "liveries"
                    / "pmdg"
                    / "My Direct Livery"
                    / "texture.TEST"
                    / "texture.cfg"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()

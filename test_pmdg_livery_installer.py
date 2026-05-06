import json
import shutil
import unittest
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from pmdg_livery_installer import InstallerError, find_pmdg_product_roots, install_livery


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


def livery_package_for(package: Path) -> Path:
    return package.parent / f"{package.name}-liveries"


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
                archive.writestr("release/manifest.json", '{"title":"download wrapper"}')
                archive.writestr(
                    "release/SimObjects/Airplanes/PMDG 737-800/liveries/pmdg/Test Livery/livery.cfg",
                    "[VERSION]\nmajor=1\nminor=0\n",
                )
                archive.writestr("release/MSFSLayoutGenerator.exe", "not copied")

            report = install_livery(zip_path, package)
            livery_package = livery_package_for(package)

            installed = (
                livery_package
                / "SimObjects"
                / "Airplanes"
                / "PMDG 737-800"
                / "liveries"
                / "pmdg"
                / "Test Livery"
                / "livery.cfg"
            )
            self.assertTrue(installed.exists())
            self.assertFalse((livery_package / "MSFSLayoutGenerator.exe").exists())
            self.assertNotIn("download wrapper", (livery_package / "manifest.json").read_text(encoding="utf-8"))
            self.assertGreater(report.layout_entries, 0)

            layout = json.loads((livery_package / "layout.json").read_text(encoding="utf-8"))
            paths = {entry["path"] for entry in layout["content"]}
            self.assertIn(
                "SimObjects/Airplanes/PMDG 737-800/liveries/pmdg/Test Livery/livery.cfg",
                paths,
            )

            self.assertTrue((livery_package / "manifest.json").exists())

    def test_zip_root_livery_uses_archive_name_not_extracted(self) -> None:
        with workspace_root() as root:
            package = make_package(root)
            zip_path = root / "Archive Named Livery.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("livery.cfg", "[VERSION]\nmajor=1\nminor=0\n")
                archive.writestr("texture.TEST/texture.cfg", "[fltsim]\n")

            install_livery(zip_path, package)
            livery_package = livery_package_for(package)

            self.assertTrue(
                (
                    livery_package
                    / "SimObjects"
                    / "Airplanes"
                    / "PMDG 737-800"
                    / "liveries"
                    / "pmdg"
                    / "Archive Named Livery"
                    / "livery.cfg"
                ).exists()
            )
            self.assertFalse(
                (
                    livery_package
                    / "SimObjects"
                    / "Airplanes"
                    / "PMDG 737-800"
                    / "liveries"
                    / "pmdg"
                    / "extracted"
                ).exists()
            )

    def test_installs_full_liveries_package_to_sibling_package(self) -> None:
        with workspace_root() as root:
            package = make_package(root)
            zip_path = root / "full-package.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr(
                    "pmdg-aircraft-738-liveries/manifest.json",
                    json.dumps({"title": "Liveries", "creator": "Source Author", "total_package_size": "0"}),
                )
                archive.writestr("pmdg-aircraft-738-liveries/layout.json", '{"content":[]}')
                archive.writestr("pmdg-aircraft-738-liveries/MSFSLayoutGenerator.exe", "not copied")
                archive.writestr(
                    "pmdg-aircraft-738-liveries/SimObjects/Airplanes/PMDG 737-800/liveries/pmdg/Full Package/livery.cfg",
                    "[VERSION]\nmajor=1\nminor=0\n",
                )

            install_livery(zip_path, package)
            livery_package = livery_package_for(package)

            self.assertTrue(
                (
                    livery_package
                    / "SimObjects"
                    / "Airplanes"
                    / "PMDG 737-800"
                    / "liveries"
                    / "pmdg"
                    / "Full Package"
                    / "livery.cfg"
                ).exists()
            )
            self.assertFalse((livery_package / "MSFSLayoutGenerator.exe").exists())
            manifest = json.loads((livery_package / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["creator"], "Source Author")

    def test_rejects_mismatched_liveries_package(self) -> None:
        with workspace_root() as root:
            package = make_package(root)
            zip_path = root / "wrong-package.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("pmdg-aircraft-77w-liveries/manifest.json", "{}")
                archive.writestr("pmdg-aircraft-77w-liveries/layout.json", '{"content":[]}')
                archive.writestr(
                    "pmdg-aircraft-77w-liveries/SimObjects/Airplanes/PMDG 777-300ER/liveries/pmdg/Wrong/livery.cfg",
                    "[VERSION]\nmajor=1\nminor=0\n",
                )

            with self.assertRaises(InstallerError):
                install_livery(zip_path, package)

    def test_rejects_source_inside_target_package(self) -> None:
        with workspace_root() as root:
            package = make_package(root)
            livery_package = livery_package_for(package)
            source = (
                livery_package
                / "SimObjects"
                / "Airplanes"
                / "PMDG 737-800"
                / "liveries"
                / "pmdg"
                / "Already Installed"
            )
            source.mkdir(parents=True)
            (source / "livery.cfg").write_text("[VERSION]\nmajor=1\nminor=0\n", encoding="utf-8")

            with self.assertRaises(InstallerError):
                install_livery(source, package)

    def test_rejects_linked_livery_target_by_default(self) -> None:
        with workspace_root() as root:
            package = make_package(root)
            livery_package_for(package).mkdir(parents=True)
            livery = root / "Linked Target Test"
            (livery / "texture.TEST").mkdir(parents=True)
            (livery / "livery.cfg").write_text("[VERSION]\nmajor=1\nminor=0\n", encoding="utf-8")

            with patch("pmdg_livery_installer.is_reparse_point", return_value=True):
                with self.assertRaises(InstallerError):
                    install_livery(livery, package)

    def test_rejects_ptp_file(self) -> None:
        with workspace_root() as root:
            package = make_package(root)
            ptp_path = root / "livery.ptp"
            with zipfile.ZipFile(ptp_path, "w") as archive:
                archive.writestr(
                    "SimObjects/Airplanes/PMDG 737-800/liveries/pmdg/Unsupported PTP/livery.cfg",
                    "[VERSION]\nmajor=1\nminor=0\n",
                )

            with self.assertRaises(InstallerError):
                install_livery(ptp_path, package)

    def test_zip_containing_only_ptp_is_not_supported(self) -> None:
        with workspace_root() as root:
            package = make_package(root)
            ptp_path = root / "inner.ptp"
            with zipfile.ZipFile(ptp_path, "w") as archive:
                archive.writestr(
                    "SimObjects/Airplanes/PMDG 737-800/liveries/pmdg/Nested Unsupported PTP/livery.cfg",
                    "[VERSION]\nmajor=1\nminor=0\n",
                )

            outer_zip = root / "download.zip"
            with zipfile.ZipFile(outer_zip, "w") as archive:
                archive.write(ptp_path, "downloaded/inner.ptp")

            with self.assertRaises(InstallerError):
                install_livery(outer_zip, package)

    def test_installs_direct_livery_folder(self) -> None:
        with workspace_root() as root:
            package = make_package(root)
            livery = root / "My Direct Livery"
            (livery / "texture.TEST").mkdir(parents=True)
            (livery / "livery.cfg").write_text("[VERSION]\nmajor=1\nminor=0\n", encoding="utf-8")
            (livery / "texture.TEST" / "texture.cfg").write_text("[fltsim]\n", encoding="utf-8")

            install_livery(livery, package)
            livery_package = livery_package_for(package)

            self.assertTrue(
                (
                    livery_package
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

    def test_detects_known_737_products_from_community(self) -> None:
        with workspace_root() as root:
            community = root / "Community"
            community.mkdir(parents=True)
            for package_name in ("pmdg-aircraft-736", "pmdg-aircraft-737", "pmdg-aircraft-739"):
                (community / package_name).mkdir(parents=True)

            products = find_pmdg_product_roots(community)
            names = {product.name for product in products}

            self.assertIn("pmdg-aircraft-736", names)
            self.assertIn("pmdg-aircraft-737", names)
            self.assertIn("pmdg-aircraft-739", names)

    def test_installs_direct_livery_for_virtual_737_600_product(self) -> None:
        with workspace_root() as root:
            community = root / "Community"
            community.mkdir(parents=True)
            package = community / "pmdg-aircraft-736"
            livery = root / "Virtual 736 Livery"
            (livery / "texture.TEST").mkdir(parents=True)
            (livery / "livery.cfg").write_text("[VERSION]\nmajor=1\nminor=0\n", encoding="utf-8")
            (livery / "texture.TEST" / "texture.cfg").write_text("[fltsim]\n", encoding="utf-8")

            install_livery(livery, package)
            installed = (
                community
                / "pmdg-aircraft-736-liveries"
                / "SimObjects"
                / "Airplanes"
                / "PMDG 737-600"
                / "liveries"
                / "pmdg"
                / "Virtual 736 Livery"
                / "livery.cfg"
            )

            self.assertTrue(installed.exists())

    def test_installs_full_livery_package_for_virtual_737_900_product(self) -> None:
        with workspace_root() as root:
            community = root / "Community"
            community.mkdir(parents=True)
            package = community / "pmdg-aircraft-739"
            zip_path = root / "739-package.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("pmdg-aircraft-739-liveries/manifest.json", "{}")
                archive.writestr("pmdg-aircraft-739-liveries/layout.json", '{"content":[]}')
                archive.writestr(
                    "pmdg-aircraft-739-liveries/SimObjects/Airplanes/PMDG 737-900/liveries/pmdg/Virtual 739/livery.cfg",
                    "[VERSION]\nmajor=1\nminor=0\n",
                )

            install_livery(zip_path, package)
            installed = (
                community
                / "pmdg-aircraft-739-liveries"
                / "SimObjects"
                / "Airplanes"
                / "PMDG 737-900"
                / "liveries"
                / "pmdg"
                / "Virtual 739"
                / "livery.cfg"
            )

            self.assertTrue(installed.exists())


if __name__ == "__main__":
    unittest.main()

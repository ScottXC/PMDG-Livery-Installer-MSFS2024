import json
import os
import subprocess
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pmdg_livery_installer as app
from livery_workflow import OperationCancelled, OperationControl, PackageTransaction, operation_context
from test_pmdg_livery_installer import make_package, livery_package_for, workspace_root


def make_livery(root, name="Test", data="original"):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "livery.cfg").write_text('[fltsim.0]\ntitle="Test Airline"\natc_id="N123XY"\n', encoding="utf-8")
    (folder / "texture.TEST").mkdir(exist_ok=True)
    (folder / "texture.TEST" / "texture.dds").write_text(data, encoding="utf-8")
    return folder


def contents(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class WorkflowTests(unittest.TestCase):
    def installed(self, root):
        package = make_package(root)
        source = make_livery(root)
        app.install_livery(source, package)
        return package, source, livery_package_for(package)

    def test_preflight_lists_real_destination_and_never_creates_package(self):
        with workspace_root() as root:
            package = make_package(root)
            plan = app.preview_install(make_livery(root), package)
            self.assertEqual(plan.liveries, ["PMDG 737-800/Test"])
            self.assertEqual(len(plan.files), 2)
            self.assertEqual(plan.target, livery_package_for(package))
            self.assertFalse(plan.target.exists())
            self.assertTrue(plan.warnings)

    def test_conflict_rejected_before_any_file_changes(self):
        with workspace_root() as root:
            package, source, target = self.installed(root)
            before = contents(target)
            (source / "new.txt").write_text("must not leak")
            plan = app.preview_install(source, package)
            self.assertEqual(len(plan.conflicts), 2)
            with self.assertRaisesRegex(app.InstallerError, "already exist"):
                app.install_livery(source, package)
            self.assertEqual(contents(target), before)

    def test_failed_overwrite_keeps_files_layout_and_manifest(self):
        with workspace_root() as root:
            package, source, target = self.installed(root)
            before = contents(target)
            (source / "texture.TEST" / "texture.dds").write_text("replacement")
            with patch.object(app, "rebuild_layout", side_effect=app.InstallerError("generator failed")):
                with self.assertRaisesRegex(app.InstallerError, "generator failed"):
                    app.install_livery(source, package, overwrite=True, backup_layout=False)
            self.assertEqual(contents(target), before)
            self.assertFalse(list(target.parent.glob(".pmdg-stage-*")))

    def test_failed_first_install_does_not_leave_empty_package(self):
        with workspace_root() as root:
            package = make_package(root)
            with patch.object(app, "rebuild_layout", side_effect=app.InstallerError("failed")):
                with self.assertRaises(app.InstallerError):
                    app.install_livery(make_livery(root), package)
            self.assertFalse(livery_package_for(package).exists())

    def test_copy_failure_after_one_file_does_not_publish_partial_install(self):
        with workspace_root() as root:
            package, source, target = self.installed(root)
            before = contents(target)
            real_copy = app.copy_file
            calls = []
            def fail_later(src, dst):
                calls.append(src)
                if len(calls) == 2:
                    raise OSError("disk full")
                return real_copy(src, dst)
            with patch.object(app, "copy_file", side_effect=fail_later):
                with self.assertRaisesRegex(OSError, "disk full"):
                    app.install_livery(source, package, overwrite=True)
            self.assertEqual(contents(target), before)

    def test_failed_uninstall_keeps_original_livery(self):
        with workspace_root() as root:
            package, _, target = self.installed(root)
            before = contents(target)
            with patch.object(app, "rebuild_layout", side_effect=app.InstallerError("bad index")):
                with self.assertRaises(app.InstallerError):
                    app.uninstall_livery(package, "Test")
            self.assertEqual(contents(target), before)

    def test_batch_uninstall_is_all_or_nothing(self):
        with workspace_root() as root:
            package, _, target = self.installed(root)
            app.install_livery(make_livery(root, "Second"), package)
            before = contents(target)
            with self.assertRaises(app.InstallerError):
                app.uninstall_liveries(package, ["Test", "Missing"])
            self.assertEqual(contents(target), before)
            reports = app.uninstall_liveries(package, ["Test", "Second"])
            self.assertEqual(len(reports), 2)
            self.assertEqual(reports[0].recovery_path, reports[1].recovery_path)
            self.assertEqual(app.list_installed_liveries(package), [])
            app.validate_layout(target)

    def test_uninstall_backup_can_restore_files_and_preserves_current_state(self):
        with workspace_root() as root:
            package, _, target = self.installed(root)
            before = contents(target)
            report = app.uninstall_livery(package, "Test")
            self.assertEqual(contents(report.recovery_path / "package"), before)
            app.restore_package_backup(package, report.recovery_path)
            self.assertEqual(len(app.list_installed_liveries(package)), 1)
            for relative, data in before.items():
                if relative not in {"manifest.json", "layout.json"}:
                    self.assertEqual((target / relative).read_bytes(), data)
            self.assertEqual(len(list(app.backup_directory(target).glob("*/package"))), 2)

    def test_successful_overwrite_retains_full_old_package_even_without_layout_backup(self):
        with workspace_root() as root:
            package, source, target = self.installed(root)
            before = contents(target)
            (source / "texture.TEST" / "texture.dds").write_text("replacement")
            report = app.install_livery(source, package, overwrite=True, backup_layout=False)
            self.assertEqual(contents(report.recovery_path / "package"), before)
            self.assertIsNone(report.backup_path)

    def test_cancel_before_commit_preserves_active_package(self):
        with workspace_root() as root:
            package, source, target = self.installed(root)
            before = contents(target)
            control = OperationControl()
            control.notify = lambda message: control.cancelled.set() if "Checking for changes before saving" in message else None
            with operation_context(control), self.assertRaises(OperationCancelled):
                app.install_livery(source, package, overwrite=True)
            self.assertEqual(contents(target), before)

    def test_cancel_during_uninstall_does_not_delete_active_files(self):
        with workspace_root() as root:
            package, _, target = self.installed(root)
            before = contents(target)
            control = OperationControl()
            control.notify = lambda message: control.cancelled.set() if "Removing from staged" in message else None
            with operation_context(control), self.assertRaises(OperationCancelled):
                app.uninstall_livery(package, "Test")
            self.assertEqual(contents(target), before)

    def test_publish_rename_failure_restores_original(self):
        with workspace_root() as root:
            package, source, target = self.installed(root)
            before = contents(target)
            rename = Path.rename
            def fail_stage(path, destination):
                if path.name.startswith(".pmdg-stage-"):
                    raise PermissionError("locked by simulator")
                return rename(path, destination)
            with patch.object(Path, "rename", fail_stage), self.assertRaisesRegex(app.InstallerError, "original files were restored"):
                app.install_livery(source, package, overwrite=True)
            self.assertEqual(contents(target), before)

    def test_external_change_blocks_commit_and_retains_external_edit(self):
        with workspace_root() as root:
            package, source, target = self.installed(root)
            rebuild = app.rebuild_layout
            def concurrent_edit(stage, backup=True):
                result = rebuild(stage, backup)
                (target / "external.txt").write_text("another tool's change")
                return result
            with patch.object(app, "rebuild_layout", side_effect=concurrent_edit), self.assertRaisesRegex(app.InstallerError, "changed during"):
                app.install_livery(source, package, overwrite=True)
            self.assertEqual((target / "external.txt").read_text(), "another tool's change")

    def test_failed_publish_and_rollback_leave_recoverable_original_files(self):
        with workspace_root() as root:
            package, source, target = self.installed(root)
            before = contents(target)
            rename = Path.rename
            def fail_new_and_rollback(path, destination):
                if path != target:
                    raise PermissionError("folder is locked")
                return rename(path, destination)
            with patch.object(Path, "rename", fail_new_and_rollback), self.assertRaisesRegex(app.InstallerError, "original files are retained"):
                app.install_livery(source, package, overwrite=True)
            backup = next(app.backup_directory(target).glob("*/package"))
            self.assertEqual(contents(backup), before)
            app.restore_package_backup(package, backup.parent)
            self.assertEqual(len(app.list_installed_liveries(package)), 1)

    def test_rebuild_repairs_companion_index_without_touching_aircraft(self):
        with workspace_root() as root:
            package, _, target = self.installed(root)
            aircraft = contents(package)
            (target / "layout.json").unlink()
            result = app.rebuild_livery_layout(package)
            self.assertEqual(result[0], target)
            self.assertEqual(contents(package), aircraft)
            self.assertGreater(app.validate_layout(target)[0], 0)

    def test_invalid_generator_json_is_failure_not_zero_entry_success(self):
        with workspace_root() as root:
            package, _, target = self.installed(root)
            before = contents(target)
            process = MagicMock(returncode=0)
            process.poll.return_value = 0
            def start(args, **kwargs):
                Path(args[1]).write_text("{broken json")
                process.communicate.return_value = ("", "")
                return process
            with patch.object(app.subprocess, "Popen", side_effect=start), self.assertRaisesRegex(app.InstallerError, "unreadable"):
                app.rebuild_livery_layout(package)
            self.assertEqual(contents(target), before)

    def test_layout_checks_missing_stale_duplicate_and_size_mismatch(self):
        with workspace_root() as root:
            package, _, target = self.installed(root)
            original = json.loads((target / "layout.json").read_text())
            entries = original["content"]
            variants = [[], [*entries, entries[0]], [*entries, {"path": "absent.dds", "size": 1}],
                        [{**entry, "size": 999} for entry in entries]]
            for content in variants:
                with self.subTest(content=content):
                    (target / "layout.json").write_text(json.dumps({"content": content}))
                    with self.assertRaises(app.InstallerError):
                        app.validate_layout(target)

    def test_generator_timeout_kills_process_and_preserves_active_package(self):
        with workspace_root() as root:
            package, _, target = self.installed(root)
            before = contents(target)
            process = MagicMock()
            process.communicate.side_effect = [subprocess.TimeoutExpired("generator", 0.1), ("", "")]
            process.poll.return_value = None
            with patch.object(app.subprocess, "Popen", return_value=process), patch.object(app.time, "monotonic", side_effect=[0, 121]):
                with self.assertRaisesRegex(app.InstallerError, "timed out"):
                    app.rebuild_livery_layout(package)
            process.kill.assert_called_once()
            self.assertEqual(contents(target), before)

    def test_ambiguous_archive_does_not_silently_install_first_package(self):
        with workspace_root() as root:
            package = make_package(root)
            source = root / "multi.zip"
            with zipfile.ZipFile(source, "w") as archive:
                for model, aircraft in (("738", "PMDG 737-800"), ("77w", "PMDG 777-300ER")):
                    archive.writestr(f"pmdg-aircraft-{model}-liveries/SimObjects/Airplanes/{aircraft}/liveries/pmdg/Test/livery.cfg", "[version]")
            with self.assertRaisesRegex(app.InstallerError, "Multiple"):
                app.install_livery(source, package)
            self.assertFalse(livery_package_for(package).exists())

    def test_simobjects_aircraft_mismatch_rejected(self):
        with workspace_root() as root:
            package = make_package(root)
            source = root / "wrong.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("SimObjects/Airplanes/PMDG 777-300ER/liveries/pmdg/Test/livery.cfg", "[version]")
            with self.assertRaisesRegex(app.InstallerError, "mismatch"):
                app.install_livery(source, package)

    def test_direct_livery_with_wrong_aircraft_reference_rejected(self):
        with workspace_root() as root:
            package = make_package(root)
            source = make_livery(root)
            (source / "livery.cfg").write_text('base_container="..\\PMDG 777-300ER"')
            with self.assertRaisesRegex(app.InstallerError, "reference mismatch"):
                app.install_livery(source, package)

    def test_zip_traversal_and_windows_drive_paths_rejected(self):
        with workspace_root() as root:
            for filename in ("../escape.cfg", "C:/escape.cfg", "dir/file:stream", "dir./escape.cfg"):
                source = root / "bad.zip"
                with zipfile.ZipFile(source, "w") as archive:
                    archive.writestr(filename, "invalid")
                with self.subTest(filename=filename), self.assertRaises(app.InstallerError):
                    app.safe_extract_archive(source, root / "extract")

    def test_export_import_roundtrip_selected_livery_only(self):
        with workspace_root() as root:
            package, _, _ = self.installed(root)
            app.install_livery(make_livery(root, "Other"), package)
            archive = app.export_liveries(package, ["Test"], root / "export.zip")
            other_package = make_package(root / "AnotherInstall")
            app.install_livery(archive, other_package)
            self.assertEqual([l.name for l in app.list_installed_liveries(other_package)], ["Test"])

    def test_restore_wrong_package_rejected(self):
        with workspace_root() as root:
            package, _, _ = self.installed(root)
            report = app.uninstall_livery(package, "Test")
            other = make_package(root / "Other")
            with self.assertRaisesRegex(app.InstallerError, "different package"):
                app.restore_package_backup(other, report.recovery_path)

    def test_restore_after_interrupted_publish_recovers_missing_package(self):
        import shutil
        with workspace_root() as root:
            package, source, target = self.installed(root)
            report = app.install_livery(source, package, overwrite=True)
            shutil.rmtree(target)
            # A stale dead-owner lock represents an interrupted process.
            lock = target.parent / f".{target.name}.pmdg-lock"
            lock.mkdir()
            (lock / "owner.json").write_text(json.dumps({"pid": 123456789}))
            with patch("livery_workflow.process_running", return_value=False):
                app.restore_package_backup(package, report.recovery_path)
            self.assertEqual(len(app.list_installed_liveries(package)), 1)

    def test_shared_texture_fallback_is_not_misreported_as_aircraft_mismatch(self):
        with workspace_root() as root:
            package = make_package(root)
            source = make_livery(root)
            (source / "texture.TEST" / "texture.cfg").write_text('fallback.1="..\\..\\PMDG 737-700\\texture.common"')
            plan = app.preview_install(source, package)
            self.assertEqual(plan.liveries, ["PMDG 737-800/Test"])

    def test_direct_source_uses_actual_installed_variant_folder(self):
        with workspace_root() as root:
            package = make_package(root)
            airplanes = package / "SimObjects" / "Airplanes"
            (airplanes / "PMDG 737-800").rename(airplanes / "PMDG 737-800BW")
            source = make_livery(root)
            (source / "livery.cfg").write_text('base_container="..\\PMDG 737-800BW"')
            plan = app.preview_install(source, package)
            self.assertEqual(plan.liveries, ["PMDG 737-800BW/Test"])
            app.install_livery(source, package)
            self.assertEqual(app.list_installed_liveries(package)[0].aircraft_name, "PMDG 737-800BW")

    def test_explicit_variant_mismatch_is_rejected(self):
        with workspace_root() as root:
            package = make_package(root)
            airplanes = package / "SimObjects" / "Airplanes"
            (airplanes / "PMDG 737-800").rename(airplanes / "PMDG 737-800BW")
            source = make_livery(root)
            (source / "livery.cfg").write_text('base_container="..\\PMDG 737-800SSW"')
            with self.assertRaisesRegex(app.InstallerError, "reference mismatch"):
                app.install_livery(source, package)

    def test_search_matches_registration_and_airline(self):
        with workspace_root() as root:
            package, _, _ = self.installed(root)
            liveries = app.list_installed_liveries(package)
            self.assertEqual(app.filter_liveries(liveries, "airline n123xy"), liveries)
            self.assertEqual(app.filter_liveries(liveries, "unknown"), [])
            self.assertEqual(app.filter_liveries(liveries, "", "Missing thumbnail"), liveries)

    def test_diagnostics_distinguishes_changed_file_from_damage(self):
        with workspace_root() as root:
            package, _, target = self.installed(root)
            texture = next(target.rglob("*.dds"))
            texture.write_text("modified by another tool")
            report = app.diagnose_package(package)
            self.assertIn("FAIL:", report)
            self.assertIn("not necessarily damaged", report)
            self.assertIn("UNKNOWN:", report)

    def test_report_redacts_case_insensitive_windows_paths(self):
        result = app.redact_report(r"Target: C:\Users\ExampleUser\Community\test", [Path(r"c:\users\exampleuser")])
        self.assertNotIn("ExampleUser", result)
        self.assertIn("<PATH_", result)

    def test_temporary_workspace_propagates_body_oserror_once(self):
        with workspace_root() as root:
            calls = []
            with self.assertRaisesRegex(OSError, "body error"):
                with app.temporary_workspace(root / "package"):
                    calls.append(1)
                    raise OSError("body error")
            self.assertEqual(calls, [1])

    def test_live_package_lock_blocks_second_operation(self):
        with workspace_root() as root:
            package, source, target = self.installed(root)
            with PackageTransaction(target, "test"):
                with self.assertRaisesRegex(app.InstallerError, "busy"):
                    app.install_livery(source, package, overwrite=True)

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_linked_target_requires_opt_in_and_keeps_junction(self):
        import _winapi
        with workspace_root() as root:
            package = make_package(root)
            target = livery_package_for(package)
            real = root / "Library" / "Custom738"
            real.mkdir(parents=True)
            _winapi.CreateJunction(str(real), str(target))
            try:
                source = make_livery(root)
                with self.assertRaisesRegex(app.InstallerError, "junction"):
                    app.install_livery(source, package)
                app.install_livery(source, package, allow_linked_targets=True)
                self.assertTrue(app.is_reparse_point(target))
                self.assertTrue(next(real.rglob("livery.cfg")).is_file())
            finally:
                target.rmdir()


if __name__ == "__main__":
    unittest.main()

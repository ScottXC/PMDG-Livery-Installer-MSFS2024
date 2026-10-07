"""Hidden-window smoke tests for the real Tk event loop and worker callbacks."""
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pmdg_livery_installer as installer
from livery_workflow import checkpoint
from test_livery_workflow import make_livery
from test_pmdg_livery_installer import make_package, workspace_root


class GuiWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workspace = workspace_root()
        self.root = self.workspace.__enter__()
        self.environment = patch.dict(os.environ, {"APPDATA": str(self.root)})
        self.environment.start()
        self.window = installer.launch_gui(run_mainloop=False, detect_on_start=False)
        self.window.withdraw()

    def tearDown(self):
        if self.window.winfo_exists():
            self.window.destroy()
        self.environment.stop()
        self.workspace.__exit__(None, None, None)

    def pump(self, condition, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.window.update()
            if condition():
                return
            time.sleep(0.01)
        self.fail("The GUI did not reach its expected state before the timeout")

    def test_worker_keeps_event_loop_responsive_and_delivers_result_on_ui_thread(self):
        threads, ticks = [], []
        release = threading.Event()
        main_thread = threading.get_ident()
        def work():
            threads.append(threading.get_ident())
            release.wait(2)
            return "done"
        def result(value):
            threads.append(threading.get_ident())
            self.assertEqual(value, "done")
        self.window.run_job("Test worker", work, result)
        self.window.after(20, lambda: ticks.append(1))
        try:
            self.pump(lambda: bool(ticks))
            self.assertTrue(self.window.busy)
            self.assertEqual(str(self.window.package_combo.cget("state")), "disabled")
        finally:
            release.set()
        self.pump(lambda: not self.window.busy)
        self.assertNotEqual(threads[0], main_thread)
        self.assertEqual(threads[1], main_thread)
        self.assertEqual(str(self.window.package_combo.cget("state")), "readonly")

    def test_cancel_returns_interface_to_usable_state(self):
        def work():
            while True:
                checkpoint()
                time.sleep(0.01)
        self.window.run_job("Cancellable scan", work, lambda _: self.fail("Cancelled work reported success"))
        self.window.cancel_job()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.window.status_var.get(), "Cancelled")
        self.assertEqual(str(self.window.cancel_button.cget("state")), "disabled")

    def test_review_install_scan_search_and_diagnose(self):
        import tkinter as tk
        package = make_package(self.root)
        source = make_livery(self.root)
        self.window.community_var.set(str(package.parent))
        self.window.apply_packages([package])
        self.pump(lambda: not self.window.busy)
        self.window.livery_var.set(str(source))
        self.window.install_selected()
        self.pump(lambda: not self.window.busy)
        dialogs = [widget for widget in self.window.winfo_children() if isinstance(widget, tk.Toplevel)]
        self.assertEqual(len(dialogs), 1)
        self.assertFalse(installer.ensure_livery_package_root(package).exists())
        install_button = next(widget for widget in self.window.walk_widgets(dialogs[0])
                              if isinstance(widget, tk.Button) and widget.cget("text") == "Install These Liveries")
        with patch("tkinter.messagebox.showinfo"), patch("tkinter.messagebox.showerror") as errors:
            install_button.invoke()
            self.pump(lambda: not self.window.busy and len(self.window.installed_liveries) == 1)
            errors.assert_not_called()
        self.assertEqual(len(self.window.installed_tree.get_children()), 1)
        self.window.search_var.set("N123XY")
        self.assertEqual(len(self.window.installed_tree.get_children()), 1)
        self.window.search_var.set("does not exist")
        self.assertFalse(self.window.installed_tree.get_children())
        self.window.search_var.set("")
        self.window.run_diagnostics()
        self.pump(lambda: not self.window.busy)
        self.assertIn("PASS: Index matches", self.window.diagnostics_text.get("1.0", "end"))

    def test_multiple_detected_communities_require_explicit_selection(self):
        import tkinter as tk
        first = installer.CommunityCandidate(self.root / "First", (), ("pmdg-aircraft-738",), True)
        second = installer.CommunityCandidate(self.root / "Second", (), ("pmdg-aircraft-77w",), True)
        self.window.choose_detected_path([first, second])
        self.assertEqual(self.window.community_var.get(), "")
        dialog = next(widget for widget in self.window.winfo_children() if isinstance(widget, tk.Toplevel))
        choices = next(widget for widget in dialog.winfo_children() if isinstance(widget, tk.Listbox))
        choices.selection_clear(0, "end")
        choices.selection_set(1)
        button = next(widget for widget in dialog.winfo_children() if isinstance(widget, tk.Button))
        button.invoke()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.window.community_var.get(), str(second.path))

    def test_editing_community_invalidates_old_package_selection(self):
        package = make_package(self.root)
        self.window.community_var.set(str(package.parent))
        self.window.apply_packages([package])
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.window.get_selected_package(), package)
        self.window.community_var.set(str(self.root / "DifferentCommunity"))
        self.assertIsNone(self.window.get_selected_package())

    def test_product_refresh_preserves_explicit_aircraft_selection(self):
        first = make_package(self.root)
        second = first.parent / "pmdg-aircraft-737"
        (second / "SimObjects" / "Airplanes" / "PMDG 737-700").mkdir(parents=True)
        (second / "layout.json").write_text('{"content":[]}')
        (second / "manifest.json").write_text('{}')
        self.window.community_var.set(str(first.parent))
        self.window.apply_packages([first, second])
        self.pump(lambda: not self.window.busy)
        label = next(label for label, path in self.window.package_paths.items() if path == second)
        self.window.package_var.set(label)
        self.window.apply_packages([first, second])
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.window.get_selected_package(), second)
        self.assertEqual(self.window.product_listbox.curselection(), (1,))

    def test_source_thumbnail_survives_zip_cleanup_and_preview_runs_on_main_thread(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Install Pillow to test source previews")
        import zipfile
        import tkinter as tk
        package = make_package(self.root)
        source = make_livery(self.root)
        Image.new("RGB", (200, 100), "blue").save(source / "thumbnail.jpg")
        archive_path = self.root / "preview.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for file in source.rglob("*"):
                if file.is_file():
                    archive.write(file, file.relative_to(source))
        self.window.community_var.set(str(package.parent))
        self.window.apply_packages([package])
        self.pump(lambda: not self.window.busy)
        self.window.livery_var.set(str(archive_path))
        self.window.install_selected()
        self.pump(lambda: not self.window.busy)
        dialog = next(widget for widget in self.window.winfo_children() if isinstance(widget, tk.Toplevel))
        images = [widget for widget in dialog.winfo_children() if isinstance(widget, tk.Label) and widget.cget("image")]
        self.assertEqual(len(images), 1)

    def test_gallery_multiselect_pagination_search_and_table_share_selection(self):
        from types import SimpleNamespace
        package = make_package(self.root)
        self.window.installed_liveries = [installer.InstalledLivery(
            package, "PMDG 737-800", f"Demo {i:02}", self.root / str(i), None,
            1, 1, 1024, 0, {"atc_id": f"DEMO-{i:02}"}) for i in range(30)]
        self.window.render_liveries()
        gallery = self.window.gallery
        self.assertEqual(len(gallery.cards), 24)
        gallery.choose("3")
        gallery.choose("5", SimpleNamespace(state=4))
        self.assertEqual(self.window.installed_tree.selection(), ("3", "5"))
        self.window.toggle_library_view()
        self.assertFalse(self.window.gallery_mode)
        self.assertEqual(len(self.window.selected_liveries()), 2)
        self.window.installed_tree.selection_set("9")
        self.window.on_installed_livery_select()
        self.window.toggle_library_view()
        self.assertEqual(gallery.cards["9"].cget("highlightbackground"), self.window.color("cyan"))
        gallery.change_page(1)
        self.assertEqual(len(gallery.cards), 6)
        gallery.select_all()
        self.assertEqual(len(self.window.selected_liveries()), 30)
        self.window.search_var.set("DEMO-29")
        self.assertEqual(len(gallery.cards), 1)
        self.assertEqual(gallery.page, 0)
        self.assertEqual(self.window.selected_liveries()[0].name, "Demo 29")
        gallery.choose("0", SimpleNamespace(state=4))
        self.assertEqual(self.window.selection_count.get(), "0 selected")
        self.assertIn("Select a livery", self.window.installed_detail_text.get("1.0", "end"))

    def test_gallery_thumbnail_decodes_and_busy_state_blocks_selection(self):
        from PIL import Image
        package = make_package(self.root)
        path = self.root / "thumbnail.png"
        Image.new("RGB", (600, 300), "blue").save(path)
        self.window.installed_liveries = [installer.InstalledLivery(
            package, "PMDG 737-800", "Blue", self.root, path, 1, 1, 1024, 0, {})]
        self.window.render_liveries()
        self.pump(lambda: bool(self.window.gallery.images))
        self.assertIn("0", self.window.gallery.images)
        from types import SimpleNamespace
        self.window.busy = True
        self.window.gallery.choose("0", SimpleNamespace(state=4))
        self.window.gallery.select_all()
        self.assertEqual(self.window.installed_tree.selection(), ("0",))
        self.window.busy = False


if __name__ == "__main__":
    unittest.main()

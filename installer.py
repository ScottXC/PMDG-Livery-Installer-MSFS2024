#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


APP_NAME = "PMDG Livery Installer MSFS2024"
VERSION = "0.1.2"
APP_EXE_NAME = f"{APP_NAME}.exe"
SETUP_TITLE = f"{APP_NAME} Setup v{VERSION}"


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


def default_install_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "Programs" / APP_NAME


def create_shortcut(link_path: Path, target_path: Path, working_dir: Path, icon_path: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$shortcut = $shell.CreateShortcut('{str(link_path)}'); "
        f"$shortcut.TargetPath = '{str(target_path)}'; "
        f"$shortcut.WorkingDirectory = '{str(working_dir)}'; "
        f"$shortcut.IconLocation = '{str(icon_path)}'; "
        "$shortcut.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )


def write_uninstaller(install_dir: Path) -> None:
    uninstall_cmd = install_dir / "Uninstall.cmd"
    uninstall_ps1 = install_dir / "Uninstall.ps1"
    start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME
    desktop_link = Path.home() / "Desktop" / f"{APP_NAME}.lnk"
    ps1 = f"""$ErrorActionPreference = 'SilentlyContinue'
Remove-Item -LiteralPath '{desktop_link}' -Force
Remove-Item -LiteralPath '{start_menu}' -Recurse -Force
$installDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Sleep -Milliseconds 300
Remove-Item -LiteralPath $installDir -Recurse -Force
"""
    uninstall_ps1.write_text(ps1, encoding="utf-8")
    uninstall_cmd.write_text(
        '@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Uninstall.ps1"\r\n',
        encoding="ascii",
    )


def install_app(install_dir: Path, desktop_shortcut: bool, start_menu_shortcut: bool) -> Path:
    source_exe = resource_path(f"payload/{APP_EXE_NAME}")
    source_icon = resource_path("payload/pmdg_livery_installer_icon.ico")
    if not source_exe.exists():
        raise FileNotFoundError(f"Bundled application executable not found: {source_exe}")

    install_dir.mkdir(parents=True, exist_ok=True)
    target_exe = install_dir / APP_EXE_NAME
    target_icon = install_dir / "pmdg_livery_installer_icon.ico"
    shutil.copy2(source_exe, target_exe)
    if source_icon.exists():
        shutil.copy2(source_icon, target_icon)
    else:
        target_icon = target_exe
    (install_dir / "VERSION.txt").write_text(VERSION + "\n", encoding="ascii")
    write_uninstaller(install_dir)

    if start_menu_shortcut:
        start_menu_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME
        create_shortcut(start_menu_dir / f"{APP_NAME}.lnk", target_exe, install_dir, target_icon)
        create_shortcut(start_menu_dir / "Uninstall.lnk", install_dir / "Uninstall.cmd", install_dir, target_icon)

    if desktop_shortcut:
        create_shortcut(Path.home() / "Desktop" / f"{APP_NAME}.lnk", target_exe, install_dir, target_icon)

    return target_exe


def launch_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title(SETUP_TITLE)
    root.geometry("720x420")
    root.minsize(680, 380)
    root.configure(bg="#0b0f14")
    icon_path = resource_path("payload/pmdg_livery_installer_icon.ico")
    if icon_path.exists():
        root.iconbitmap(str(icon_path))

    install_dir_var = tk.StringVar(value=str(default_install_dir()))
    desktop_var = tk.BooleanVar(value=True)
    start_menu_var = tk.BooleanVar(value=True)
    status_var = tk.StringVar(value="Ready to install")

    def browse() -> None:
        path = filedialog.askdirectory(title="Select install folder", initialdir=str(default_install_dir().parent))
        if path:
            install_dir_var.set(path)

    def run_install() -> None:
        try:
            status_var.set("Installing...")
            root.update_idletasks()
            target = install_app(Path(install_dir_var.get()), desktop_var.get(), start_menu_var.get())
        except Exception as exc:  # noqa: BLE001
            status_var.set("Install failed")
            messagebox.showerror("Install failed", str(exc))
            return
        status_var.set("Install complete")
        if messagebox.askyesno("Install complete", f"{APP_NAME} was installed.\n\nLaunch now?"):
            subprocess.Popen([str(target)], cwd=str(target.parent))
        root.destroy()

    def label(parent, text, size=10, color="#e8edf2", weight="normal"):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=("Segoe UI", size, weight), anchor="w")

    top = tk.Frame(root, bg="#080b0f", height=82)
    top.pack(fill=tk.X)
    top.pack_propagate(False)
    tk.Label(top, text="PMDG", bg="#b51f2c", fg="#ffffff", font=("Segoe UI", 13, "bold"), width=7, pady=8).pack(side=tk.LEFT, padx=22, pady=18)
    title_box = tk.Frame(top, bg="#080b0f")
    title_box.pack(side=tk.LEFT)
    label(title_box, SETUP_TITLE, 15, "#ffffff", "bold").pack(anchor="w")
    label(title_box, "Install the standalone MSFS 2024 PMDG livery manager.", 9, "#9aa7b3").pack(anchor="w")
    tk.Frame(root, bg="#b51f2c", height=3).pack(fill=tk.X)

    body = tk.Frame(root, bg="#121922", padx=22, pady=22)
    body.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
    body.columnconfigure(1, weight=1)

    label(body, "Install folder", 10, "#9aa7b3", "bold").grid(row=0, column=0, sticky="w", pady=(0, 8))
    entry = tk.Entry(body, textvariable=install_dir_var, bg="#0f151d", fg="#e8edf2", insertbackground="#e8edf2", relief=tk.FLAT, bd=0)
    entry.grid(row=0, column=1, sticky="ew", ipady=8, pady=(0, 8), padx=(12, 8))
    tk.Button(body, text="Browse", command=browse, bg="#1c2632", fg="#ffffff", relief=tk.FLAT, padx=14, pady=8).grid(row=0, column=2, pady=(0, 8))

    tk.Checkbutton(body, text="Create desktop shortcut", variable=desktop_var, bg="#121922", fg="#e8edf2", selectcolor="#0f151d", activebackground="#121922", activeforeground="#e8edf2").grid(row=1, column=1, sticky="w", pady=(16, 4))
    tk.Checkbutton(body, text="Create Start Menu shortcuts", variable=start_menu_var, bg="#121922", fg="#e8edf2", selectcolor="#0f151d", activebackground="#121922", activeforeground="#e8edf2").grid(row=2, column=1, sticky="w", pady=(0, 20))

    label(body, "Version", 10, "#9aa7b3", "bold").grid(row=3, column=0, sticky="w")
    label(body, VERSION, 10, "#e8edf2").grid(row=3, column=1, sticky="w", padx=(12, 0))
    label(body, "", 1).grid(row=4, column=0, pady=18)
    label(body, "", 1).grid(row=5, column=0)

    footer = tk.Frame(body, bg="#121922")
    footer.grid(row=6, column=0, columnspan=3, sticky="ew")
    label(footer, "", 1).pack(side=tk.LEFT, expand=True, fill=tk.X)
    tk.Label(footer, textvariable=status_var, bg="#121922", fg="#9aa7b3", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 14))
    tk.Button(footer, text="Cancel", command=root.destroy, bg="#1c2632", fg="#ffffff", relief=tk.FLAT, padx=18, pady=9).pack(side=tk.LEFT, padx=(0, 10))
    tk.Button(footer, text="Install", command=run_install, bg="#b51f2c", activebackground="#d22937", fg="#ffffff", activeforeground="#ffffff", relief=tk.FLAT, padx=22, pady=9, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)

    root.mainloop()


def main() -> int:
    if "/S" in sys.argv or "--silent" in sys.argv:
        install_app(default_install_dir(), desktop_shortcut=True, start_menu_shortcut=True)
        return 0
    launch_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

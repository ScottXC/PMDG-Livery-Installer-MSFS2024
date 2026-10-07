# PMDG Livery Installer MSFS2024

Standalone livery management for PMDG aircraft in Microsoft Flight Simulator 2024, with no dependency on PMDG Operations Center 3.

面向 Microsoft Flight Simulator 2024 PMDG 机模的独立涂装管理工具，不依赖 PMDG Operations Center 3。

![Icon](assets/pmdg_livery_installer_icon.png)

<details open>
<summary>English</summary>

## Overview

PMDG Livery Installer MSFS2024 helps you manage compatible PMDG aircraft liveries for MSFS 2024. It can find installed PMDG products, add liveries from ZIP or folder sources, check package status, run diagnostics, and keep your livery library organized.

## Features

- Multi-page interface for install, manage, product, diagnostics, and settings workflows.
- `Aircraft` page for scanning installed PMDG aircraft packages and viewing package details.
- `My liveries` page for reviewing installed liveries, previewing thumbnails, and uninstalling selected liveries.
- `Install liveries` page for installing compatible ZIP or folder liveries.
- `Diagnostics` page for path checks, package checks, and layout rebuilds.
- `Settings` page for saved paths, install behavior, and display size.
- Automatic MSFS 2024 Community path detection.
- Automatic PMDG package scanning under `Community`.
- Installs liveries into the companion `pmdg-aircraft-*-liveries` package under Community.
- Uses a bundled `MSFSLayoutGenerator.exe` to update the livery package `layout.json`.
- Supports common PMDG MSFS 2024 livery package structures, including full `*-liveries` packages and SimObjects-based ZIP packages.
- Rebuilds `layout.json` after installation.
- Rebuilds `layout.json` after uninstalling a livery.
- Updates package metadata when applicable.
- Backs up the original `layout.json` by default.

## Changes in v0.1.6

- **Rebuilt library UI:** a charcoal sidebar, original tail-fin application icon and matching navigation icons, thumbnail card grid with installed status, persistent search, an aircraft selector, and a separate details panel. Switch between Grid / List; use Ctrl/Shift or keyboard arrows to select. Large libraries are paged at 24 cards to bound rendering work. The new Install liveries page separates source, destination and review.

- **Review before installation:** shows every detected livery, the actual destination, file size, overwrite conflicts and the first available source thumbnail. Multiple aircraft packages, explicit aircraft mismatches, unsupported legacy structures and PTP-only downloads produce actionable errors.
- **Recoverable operations:** installation, overwrite, batch uninstall, layout repair and backup restoration prepare a separate package copy, verify it, then publish it. Copy failures, generator failures and cancellation before publication leave the active package intact. File copies are checked with SHA-256; the generated index is checked for missing, stale, duplicate and size-mismatched entries.
- **Responsive interface:** scans and package operations run on a worker. The status shows the current phase/file; the bar indicates activity, not an estimated percentage. Cancel waits for a safe stopping point. The final directory swap finishes without interruption. Closing the window during a task requests cancellation and waits. Layout generation has a 120-second timeout.
- **Choose the correct installation:** multiple detected Community locations show their configuration sources, detected PMDG products and an OS estimate of write access. A manually edited Community path requires refreshing products before installation.
- **Manage a library:** search aircraft, livery names, airline metadata or registration; filter missing thumbnails; Ctrl/Shift-select liveries to uninstall or export to ZIP. Exported ZIPs can be reimported through Review & Install. Thumbnails decode in the background, with a bounded display cache.
- **Useful diagnostics:** PASS / FAIL / UNKNOWN results distinguish file modifications from proven damage. Rebuild Livery Layout operates on the companion `*-liveries` package. Export Report saves a text report with known personal and installation paths hidden by default.

### Recovery backups and disk space

Full backups are independent of the optional `Keep a layout.json backup` checkbox. Before a successful change to an existing package, its complete previous state is retained in:

```text
Community/.pmdg-livery-backups/<package>/<timestamp-id>/
    recovery.json
    package/
```

With an explicitly allowed linked package, backups live beside its **real target**, and the Community junction is preserved. Nested links inside packages are rejected. Restore Backup asks for the timestamp folder containing `recovery.json`, previews the destination, and replaces the **whole companion livery package** after verification. The state being replaced is also backed up. The aircraft package itself is not changed.

Staging needs space for another copy of the current package plus incoming files. Backups are retained until you remove them; review old backups after confirming your liveries work. A process crash between the two final renames is not crash-atomic: originals remain in the recovery folder. After the old process exits, reopening the app and using Restore Backup can recover a missing package. A lock with an unreadable owner record requires manual inspection after closing other installer instances.

These checks cannot establish MSFS version, winglet/engine compatibility or in-game loading for every third-party download. Shared texture fallback references are not treated as evidence of a wrong aircraft. This release has synthetic filesystem and hidden-window GUI regression coverage, but has not been validated inside a real MSFS 2024 session. Fenix installation, official accounts/downloads, PTP conversion and MSFS 2020 conversion remain outside this tool's scope.

## Requirements

- Windows.
- PMDG aircraft installed in MSFS 2024.
- A compatible PMDG MSFS 2024 livery ZIP, ZIP-based or extracted livery folder.

This tool does not convert MSFS 2020 liveries. PTP import is not supported; export or download the livery as ZIP or an extracted folder instead.

## Download And Run

Download the installer from the GitHub Releases page and run:

```text
PMDG Livery Installer MSFS2024 Setup v0.1.6.exe
```

Portable executable builds are also available in:

```text
dist\PMDG Livery Installer MSFS2024.exe
```

## Basic Use

1. Open the app.
2. Confirm or detect the MSFS 2024 Community folder on `Install liveries`.
3. Select the target PMDG aircraft package.
4. Select a livery ZIP or extracted folder.
5. Review install options, click `Review & Install`, review the detected liveries and destination, then confirm the install.
6. Start MSFS 2024 and check the aircraft livery list.

The app installs into the matching Community livery package, for example:

```text
Community\pmdg-aircraft-77w-liveries\SimObjects\Airplanes\PMDG 777-300ER\liveries\pmdg\<livery name>
```

It then runs the bundled `MSFSLayoutGenerator.exe` against that livery package `layout.json`, replacing the manual drag-to-layout-generator step.

## Manage Installed Liveries

Use the `My liveries` page to scan the selected aircraft's companion `*-liveries` package. Selecting a livery shows its folder, metadata, file counts, size, and the first supported thumbnail found under the livery folder. `Uninstall Selected` removes the selected livery folders as one operation and verifies the rebuilt livery package index. A full recovery backup is retained.

## Safety Notes

- This v0.1.6 update has not been verified in a real MSFS 2024/PMDG installation environment. Back up your relevant Community `pmdg-aircraft-*-liveries` package before using install or uninstall features.
- ZIP files that contain a livery directly at archive root are installed using the archive file name, not the temporary extraction folder name.
- The installer refuses to install when the selected source is inside the target PMDG package, or when the target package is inside the selected source.
- Existing livery packages that are symlinks, junctions, or other Windows reparse points are blocked by default. This is intended to protect MSFS Addons Linker workflows from accidental writes into linked source folders.
- Use `Allow linked target folders` only when you intentionally want the installer to write into a linked livery package.

## Uninstall

The Inno Setup installer writes a user-level installation. You can remove the application from:

- Windows Settings > Apps.
- Start Menu > PMDG Livery Installer MSFS2024 > Uninstall.
- Command line:

```powershell
"%LOCALAPPDATA%\Programs\PMDG Livery Installer MSFS2024\unins000.exe" /VERYSILENT
```

The uninstaller removes the installed app folder, shortcuts, saved settings, and the Windows uninstall entry. It does not remove liveries already installed into MSFS Community packages.

## Command Line

Detect paths:

```powershell
python .\pmdg_livery_installer.py --detect
```

Install with a package root:

```powershell
python .\pmdg_livery_installer.py `
  --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" `
  --livery "D:\Downloads\my-pmdg-738-livery.zip"
```

List installed liveries:

```powershell
python .\pmdg_livery_installer.py `
  --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" `
  --list-liveries
```

Uninstall one installed livery:

```powershell
python .\pmdg_livery_installer.py `
  --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" `
  --uninstall-livery "My Livery Name"
```

Inspect a source without installing, check or repair the companion package, and restore a backup:

```powershell
python .\pmdg_livery_installer.py --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" --livery "D:\Downloads\livery.zip" --preflight
python .\pmdg_livery_installer.py --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" --diagnose
python .\pmdg_livery_installer.py --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" --rebuild-layout
python .\pmdg_livery_installer.py --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" --restore-backup "D:\MSFS2024\Community\.pmdg-livery-backups\pmdg-aircraft-738-liveries\<timestamp-id>"
python .\pmdg_livery_installer.py --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" --search "N123" --export-zip "D:\Backups\selected-liveries.zip"
```

## Build and tests

Install the tested build dependencies locally (Pillow is included in the packaged executable):

```powershell
python -m pip install --target .build_tools -r requirements-build.txt
$env:PYTHONPATH = Join-Path $PWD ".build_tools"
python -m unittest -v
```

Windows junction regression tests require permission to create junctions in the project test workspace. The tests use synthetic packages; they do not change an installed simulator.


```powershell
powershell -ExecutionPolicy Bypass -File .\build_installer.ps1
python .\tools\smoke_executable.py
python .\tools\package_release.py
```

The release script writes the flightsim.to upload ZIP, a portable ZIP, SHA-256 checksums, and listing text under `release/`. It never uploads to flightsim.to. The v0.1.6 release skips further Computer Use checks at the maintainer's request; filesystem, hidden-window Tk, and packaged-command tests remain required.

## Linux / Steam Proton compatibility

This application targets Windows, but has been tested successfully on Linux with the Steam version of Microsoft Flight Simulator 2024 running under Proton.

The important part is to run the installer/application inside the same Proton prefix as MSFS 2024. For the Steam version, the app ID is `2537590`:

```bash
protontricks-launch --appid 2537590 "/path/to/PMDG.Livery.Installer.MSFS2024.Setup.exe"
```
After installation, launch the installed application the same way, for example:

```bash
protontricks-launch --appid 2537590 "$HOME/.steam/steam/steamapps/compatdata/2537590/pfx/drive_c/users/steamuser/AppData/Local/Programs/PMDG Livery Installer MSFS2024/PMDG Livery Installer MSFS2024.exe"
```

### Notes

- The MSFS 2024 Proton prefix should be backed up before installing third-party tools.
- If PMDG aircraft are installed in Community2024, set the app's Community path to that folder in settings.
- Steam may think MSFS 2024 is running while this installer/app is open and will not launch until you close the installer and relaunch MSFS 2024.

</details>

<details>
<summary>中文</summary>

## 简介

PMDG Livery Installer MSFS2024 用于管理 MSFS 2024 中兼容的 PMDG 机模涂装。它可以查找已安装的 PMDG 产品，从 ZIP 或文件夹添加涂装，检查安装状态，运行诊断，并帮助整理涂装库。

## 功能

- 面向安装、管理、产品、诊断和设置工作流的多分页界面。
- `Aircraft` 页面：扫描已安装的 PMDG 机模包并查看产品详情。
- `My liveries` 页面：查看已安装涂装、预览缩略图，并卸载选中的涂装。
- `Install liveries` 页面：安装兼容的 ZIP 或文件夹涂装。
- `Diagnostics` 页面：检查路径、检查产品结构、重建 `layout.json`。
- `Settings` 页面：保存路径、安装行为和窗口尺寸。
- 自动探测 MSFS 2024 Community 路径。
- 自动扫描 `Community` 下的 PMDG 机模包。
- 将涂装安装到 Community 下对应的 `pmdg-aircraft-*-liveries` 配套包。
- 使用内置 `MSFSLayoutGenerator.exe` 更新 livery 包的 `layout.json`。
- 支持常见 PMDG MSFS 2024 涂装包结构，包括 ZIP、SimObjects 结构和完整 `*-liveries` 包。
- 安装后自动重建 `layout.json`。
- 卸载涂装后自动重建 `layout.json`。
- 在适用时更新包元数据。
- 默认备份原始 `layout.json`。

## v0.1.6 改进

- **重建涂装库界面**：深色侧栏、原创垂尾应用图标及统一导航图标、带安装状态的缩略图卡片、常驻搜索、机型选择和独立详情区。支持网格／列表切换、Ctrl/Shift 多选和方向键选中，每页最多 24 张卡片。安装页面明确分为来源、目标和预检查三个步骤。

- **安装前预检查**：显示识别到的全部涂装、实际目标、文件大小、覆盖冲突和首张可用缩略图；对多机型混装、明确的机型不匹配、旧版结构及仅含 PTP 的下载提供原因和处理提示。
- **失败可恢复**：安装、覆盖、批量卸载、索引修复和恢复操作先准备完整暂存副本，校验后再提交。复制使用 SHA-256 校验，索引检查遗漏、重复、失效路径和文件大小差异。复制失败、索引生成失败或提交前取消不会修改正在使用的包。
- **界面保持响应**：扫描和文件操作在后台执行，显示阶段和当前文件；进度条表示正在工作，不虚构百分比。取消会等待安全停止点，最后的目录切换会完成后再退出。索引生成超时为 120 秒。
- **多路径明确选择**：列出 Community 候选、配置来源、检测到的机型及系统估计的可写状态。手动修改 Community 路径后，需要刷新产品才可安装。
- **管理、迁移与恢复**：按机型、名称、航空公司或注册号搜索，筛选缺图涂装，Ctrl/Shift 多选卸载或导出 ZIP。导出的 ZIP 可通过安装页面再次导入。缩略图后台解码并缓存显示结果。
- **可执行的诊断结果**：区分 PASS / FAIL / UNKNOWN，并提供对应操作；文件修改不直接判为损坏。手动重建针对配套涂装包；诊断报告可导出，默认隐藏已知个人路径和安装位置。

### 完整备份与恢复

每次成功修改已有涂装包前，都会保留完整旧状态，与 `Keep a layout.json backup` 选项无关。默认位置为 `Community/.pmdg-livery-backups/<包名>/<时间戳-id>/`，包含 `recovery.json` 和 `package/`。允许写入链接包时，备份位于真实目标旁，Community 的 junction 保持不变；包内部的嵌套链接仍会拒绝处理。

在 My liveries 点击 `Restore Backup`，选择包含 `recovery.json` 的时间戳文件夹。恢复会替换整个配套涂装包，当前状态也会另行备份；飞机本体包不会被修改。暂存需要容纳现有包副本和新增文件的磁盘空间，历史备份不会自动清理，可在确认游戏中正常后自行整理。

最后两次目录重命名不具备断电原子性：若进程恰在此时中断，原文件仍保留在恢复目录。旧进程退出后，可重开应用并恢复备份；无法读取所属进程信息的锁需要关闭其他安装器后人工检查。

本版本有合成文件系统和隐藏窗口 GUI 回归测试，尚未经过真实 MSFS 2024 游戏会话验证。不能单凭文件保证所有第三方涂装的模拟器版本、翼梢/发动机变体或游戏加载结果。共享贴图回退引用不会被直接认定为机型错误。Fenix 安装、官方账号与下载服务、PTP 和 MSFS 2020 转换不在支持范围内。

## 要求

- Windows。
- 已在 MSFS 2024 中安装 PMDG 机模。
- 兼容 PMDG MSFS 2024 的涂装 ZIP 或已解压涂装文件夹。

本工具不转换 MSFS 2020 涂装。不支持 PTP 导入；请改用 ZIP 或已解压文件夹。

## 下载和运行

从 GitHub Releases 下载安装包并运行：

```text
PMDG Livery Installer MSFS2024 Setup v0.1.6.exe
```

便携版可执行文件也位于：

```text
dist\PMDG Livery Installer MSFS2024.exe
```

## 基本使用

1. 打开程序。
2. 在 `Install liveries` 页面确认或自动探测 MSFS 2024 Community 文件夹。
3. 选择目标 PMDG 机模包。
4. 选择涂装 ZIP 或已解压文件夹。
5. 检查安装选项，点击 `Review & Install`，核对识别出的涂装和目标后再安装。
6. 启动 MSFS 2024，在对应机型的涂装列表中检查。

程序会安装到对应的 Community livery 包，例如：

```text
Community\pmdg-aircraft-77w-liveries\SimObjects\Airplanes\PMDG 777-300ER\liveries\pmdg\<涂装名>
```

随后会调用内置 `MSFSLayoutGenerator.exe` 处理该 livery 包的 `layout.json`，无需手动把 `layout.json` 拖到 layout generator 上。

## 管理已安装涂装

进入 `My liveries` 页面后选择机型并扫描配套的 `*-liveries` 包。点击涂装会显示文件夹、元数据、文件数量、大小和可识别的缩略图。可以通过 Ctrl/Shift 多选涂装；点击 `Uninstall Selected` 会将选中的涂装作为一次操作卸载，并校验重建后的索引，同时保留完整恢复备份。

## 安全提示

本次 v0.1.6 更新未经过真实 MSFS 2024/PMDG 安装环境的实际验证。使用安装或卸载功能前，请先备份相关 Community `pmdg-aircraft-*-liveries` 包。

## 卸载

Inno Setup 安装包会写入当前用户级安装。可以通过 Windows 设置 > Apps、开始菜单卸载项，或命令行卸载：

```powershell
"%LOCALAPPDATA%\Programs\PMDG Livery Installer MSFS2024\unins000.exe" /VERYSILENT
```

卸载只删除应用本体、快捷方式、本工具保存的设置和 Windows 卸载项，不会删除 MSFS Community 中已经安装的涂装。

## 命令行

探测路径：

```powershell
python .\pmdg_livery_installer.py --detect
```

使用完整包路径安装：

```powershell
python .\pmdg_livery_installer.py `
  --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" `
  --livery "D:\Downloads\my-pmdg-738-livery.zip"
```

列出已安装涂装：

```powershell
python .\pmdg_livery_installer.py `
  --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" `
  --list-liveries
```

卸载一个已安装涂装：

```powershell
python .\pmdg_livery_installer.py `
  --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" `
  --uninstall-livery "My Livery Name"
```

## 构建

```powershell
powershell -ExecutionPolicy Bypass -File .\build_installer.ps1
python .\tools\smoke_executable.py
python .\tools\package_release.py
```

The release script writes the flightsim.to upload ZIP, a portable ZIP, SHA-256 checksums, and listing text under `release/`. It never uploads to flightsim.to. The v0.1.6 release skips further Computer Use checks at the maintainer's request; filesystem, hidden-window Tk, and packaged-command tests remain required.

构建 Inno Setup 安装包：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_installer.ps1
```

`build_installer.ps1` 需要本机安装 Inno Setup 6 或 7，并能找到 `ISCC.exe`。

</details>


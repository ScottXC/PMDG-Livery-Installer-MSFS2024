# PMDG Livery Installer MSFS2024

Standalone livery management for PMDG aircraft in Microsoft Flight Simulator 2024, with no dependency on PMDG Operations Center 3.

面向 Microsoft Flight Simulator 2024 PMDG 机模的独立涂装管理工具，不依赖 PMDG Operations Center 3。

![Icon](assets/pmdg_livery_installer_icon.png)

<details open>
<summary>English</summary>

## Overview

PMDG Livery Installer MSFS2024 helps you manage compatible PMDG aircraft liveries for MSFS 2024. It can find installed PMDG products, add liveries from ZIP, PTP, or folder sources, check package status, run diagnostics, and keep your livery library organized.

## Features

- Multi-page interface for product, livery, diagnostics, and settings workflows.
- `Products` page for scanning installed PMDG aircraft packages and viewing package details.
- `Liveries` page for installing compatible ZIP, PTP, or folder liveries.
- `Diagnostics` page for path checks, package checks, and layout rebuilds.
- `Settings` page for saved paths, install behavior, and display size.
- Automatic MSFS 2024 Community and WASM path detection.
- Automatic PMDG package scanning under `Community`.
- Supports common PMDG MSFS 2024 livery package structures, including ZIP-based `.ptp` packages.
- Rebuilds `layout.json` after installation.
- Updates package metadata when applicable.
- Backs up the original `layout.json` by default.

## Requirements

- Windows.
- PMDG aircraft installed in MSFS 2024.
- A compatible PMDG MSFS 2024 livery ZIP, ZIP-based PTP, or extracted livery folder.

This tool does not convert MSFS 2020 liveries. Proprietary or damaged PTP files that cannot be read as archives are reported as unsupported.

## Download And Run

Download the installer from the GitHub Releases page and run:

```text
PMDG Livery Installer MSFS2024 Setup v0.1.1.exe
```

Portable executable builds are also available in:

```text
dist\PMDG Livery Installer MSFS2024.exe
```

## Basic Use

1. Open the app.
2. Go to `Products` and refresh installed products.
3. Go to `Liveries`.
4. Select the target PMDG aircraft package.
5. Select a livery ZIP, PTP, or extracted folder.
6. Click `Install Livery`.
7. Start MSFS 2024 and check the aircraft livery list.

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

## Build

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

</details>

<details>
<summary>中文</summary>

## 简介

PMDG Livery Installer MSFS2024 用于管理 MSFS 2024 中兼容的 PMDG 机模涂装。它可以查找已安装的 PMDG 产品，从 ZIP、PTP 或文件夹添加涂装，检查安装状态，运行诊断，并帮助整理涂装库。

## 功能

- 面向产品、涂装、诊断和设置工作流的多分页界面。
- `Products` 页面：扫描已安装的 PMDG 机模包并查看产品详情。
- `Liveries` 页面：安装兼容的 ZIP、PTP 或文件夹涂装。
- `Diagnostics` 页面：检查路径、检查产品结构、重建 `layout.json`。
- `Settings` 页面：保存路径、安装行为和窗口尺寸。
- 自动探测 MSFS 2024 Community 和 WASM 路径。
- 自动扫描 `Community` 下的 PMDG 机模包。
- 支持常见 PMDG MSFS 2024 涂装包结构，包括 ZIP-based `.ptp` 包。
- 安装后自动重建 `layout.json`。
- 在适用时更新包元数据。
- 默认备份原始 `layout.json`。

## 要求

- Windows。
- 已在 MSFS 2024 中安装 PMDG 机模。
- 兼容 PMDG MSFS 2024 的涂装 ZIP、ZIP-based PTP 或已解压涂装文件夹。

本工具不转换 MSFS 2020 涂装。无法作为 archive 读取的专有或损坏 PTP 文件会被提示为不支持。

## 下载和运行

从 GitHub Releases 下载安装包并运行：

```text
PMDG Livery Installer MSFS2024 Setup v0.1.1.exe
```

便携版可执行文件也位于：

```text
dist\PMDG Livery Installer MSFS2024.exe
```

## 基本使用

1. 打开程序。
2. 进入 `Products` 页面并刷新已安装产品。
3. 进入 `Liveries` 页面。
4. 选择目标 PMDG 机模包。
5. 选择涂装 ZIP、PTP 或已解压文件夹。
6. 点击 `Install Livery`。
7. 启动 MSFS 2024，在对应机型的涂装列表中检查。

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

## 构建

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

</details>

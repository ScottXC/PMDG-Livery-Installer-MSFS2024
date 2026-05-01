# PMDG Livery Installer for MSFS 2024

一个不依赖 PMDG Operations Center 3 的 PMDG 涂装安装工具，面向 Microsoft Flight Simulator 2024。

## 功能

- 安装 MSFS 2024 版 PMDG livery `.zip` 或已解压文件夹。
- 自动探测 MSFS 2024 `Community`、`Community2024` 和 `WASM\MSFS2024` 常见路径。
- 自动扫描 `Community` 下的 `pmdg-aircraft-*` 包。
- 左侧 OC3 风格分页：
  - `Products`：扫描 PMDG 产品、查看 manifest/layout/飞机文件夹/涂装数量。
  - `Liveries`：选择产品并安装 ZIP 或文件夹涂装。
  - `Diagnostics`：检查路径可写性、产品结构，并可重建所选产品的 `layout.json`。
  - `Settings`：保存默认路径、覆盖/备份选项，并切换更高分辨率窗口尺寸。
- 支持两类常见涂装结构：
  - 压缩包内含 `SimObjects\Airplanes\...\liveries\pmdg\...`
  - 压缩包或文件夹本身就是一个 livery 文件夹，包含 `livery.cfg`、`texture.*`、`model.*` 或 `panel.*`
- 安装后自动重建 PMDG 包根目录的 `layout.json`。
- 若 `manifest.json` 含 `total_package_size`，会同步更新该字段。
- 默认备份原 `layout.json` 为 `layout.json.bak-YYYYMMDD-HHMMSS`。

## 前提

- Windows。
- Python 3.10 或更高版本。
- 需要先在 MSFS 2024 中安装并至少加载过对应 PMDG 机模。Marketplace 机模如果仍是 streamed 状态，通常不会有可写入的 PMDG 包目录。
- 此工具只处理 MSFS 2024 可用的 ZIP/文件夹涂装，不转换 MSFS 2020 贴图格式，也不安装 `.ptp`。

## 图形界面使用

如果使用已打包版本，直接运行：

```powershell
.\dist\"PMDG Livery Installer MSFS2024.exe"
```

双击运行：

```powershell
run_installer.bat
```

或在当前目录运行：

```powershell
python .\pmdg_livery_installer.py
```

步骤：

1. 确认或手动选择 MSFS 2024 `Community` 路径。
2. 点击 `Refresh`，选择对应的 `pmdg-aircraft-*` 包，例如 `pmdg-aircraft-738`。
3. 选择涂装 `.zip` 或已解压文件夹。
4. 点击 `Install livery`。
5. 启动 MSFS 2024 检查对应机型的涂装列表。

## 命令行使用

探测路径：

```powershell
python .\pmdg_livery_installer.py --detect
```

用完整 PMDG 包路径安装：

```powershell
python .\pmdg_livery_installer.py `
  --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" `
  --livery "D:\Downloads\my-pmdg-738-livery.zip"
```

通过 Community 和包名安装：

```powershell
python .\pmdg_livery_installer.py `
  --community "D:\MSFS2024\Community" `
  --package pmdg-aircraft-738 `
  --livery "D:\Downloads\my-pmdg-738-livery.zip"
```

如果确定要覆盖同名文件：

```powershell
python .\pmdg_livery_installer.py `
  --package-root "D:\MSFS2024\Community\pmdg-aircraft-738" `
  --livery "D:\Downloads\my-pmdg-738-livery.zip" `
  --overwrite
```

## 注意事项

- 建议关闭 MSFS 2024 后再安装涂装。
- 如果涂装作者要求把 `SimObjects` 拖进 `Community\pmdg-aircraft-738`，本工具会自动完成同等操作并更新 `layout.json`。
- 如果涂装压缩包里附带 `MSFSLayoutGenerator.exe`，本工具不会复制它，也不需要它。
- 如果安装后游戏内不显示，优先确认该涂装明确支持 MSFS 2024 和你的 PMDG 机型变体。

## 重新打包

如果修改了源码或图标，运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

输出文件位于：

```text
dist\PMDG Livery Installer MSFS2024.exe
```

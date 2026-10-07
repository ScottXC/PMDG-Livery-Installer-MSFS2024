"""Create PyInstaller Windows file properties from the public version."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_version import VERSION

version = tuple(int(n) for n in VERSION.split('.')) + (0,)
output = ROOT / 'build' / 'version_info.txt'
output.parent.mkdir(exist_ok=True)
output.write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r}, mask=0x3f,
      flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904b0', [
    StringStruct('CompanyName', 'PMDG Livery Installer MSFS2024'),
    StringStruct('FileDescription', 'PMDG Livery Installer MSFS2024'),
    StringStruct('FileVersion', '{VERSION}'),
    StringStruct('ProductName', 'PMDG Livery Installer MSFS2024'),
    StringStruct('ProductVersion', '{VERSION}'),
    StringStruct('OriginalFilename', 'PMDG Livery Installer MSFS2024.exe')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)''', encoding='utf-8')
print(f'Windows product version: {VERSION}')

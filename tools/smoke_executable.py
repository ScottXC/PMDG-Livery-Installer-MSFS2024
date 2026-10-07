"""Exercise seven packaged workflows using isolated synthetic fixtures."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from PIL import Image
from test_pmdg_livery_installer import make_package, workspace_root, livery_package_for
from test_livery_workflow import make_livery
from pmdg_livery_installer import list_installed_liveries, validate_layout, backup_directory

exe = Path.cwd() / 'dist' / 'PMDG Livery Installer MSFS2024.exe'
with workspace_root() as root:
    package = make_package(root)
    source = make_livery(root)
    Image.new('RGB', (120, 80), 'blue').save(source / 'thumbnail.jpg')
    environment = {**os.environ, 'TEMP': str(root), 'TMP': str(root), 'APPDATA': str(root)}
    def run(*arguments):
        result = subprocess.run([str(exe), '--package-root', str(package), *map(str, arguments)],
                                env=environment, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, (arguments, result.returncode)
        print('PASS', arguments[0], flush=True)
    run('--livery', source, '--preflight')
    assert not livery_package_for(package).exists()
    run('--livery', source)
    assert len(list_installed_liveries(package)) == 1
    validate_layout(livery_package_for(package))
    run('--diagnose')
    run('--export-zip', root / 'portable-export.zip')
    assert (root / 'portable-export.zip').exists()
    run('--uninstall-livery', 'Test')
    assert not list_installed_liveries(package)
    recovery = next(backup_directory(livery_package_for(package)).glob('*/recovery.json')).parent
    run('--restore-backup', recovery)
    assert len(list_installed_liveries(package)) == 1
    run('--rebuild-layout')
    validate_layout(livery_package_for(package))
print('Packaged executable smoke test passed: 7 operations', flush=True)

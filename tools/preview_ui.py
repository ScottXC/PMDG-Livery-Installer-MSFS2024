"""Open an isolated demo library for UI review and release screenshots.

All fixtures and settings stay inside .tmp/ui-preview. No simulator files are used.
The thumbnails are original schematic illustrations, not simulator screenshots.
"""
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw, ImageFont
import pmdg_livery_installer as app
from app_version import VERSION

root = ROOT / '.tmp' / 'ui-preview'
root.mkdir(parents=True, exist_ok=True)
os.environ['APPDATA'] = str(root / 'settings')
community = root / 'Community'
package = community / 'pmdg-aircraft-738'
(package / 'SimObjects' / 'Airplanes' / 'PMDG 737-800').mkdir(parents=True, exist_ok=True)
(package / 'manifest.json').write_text('{}')
(package / 'layout.json').write_text('{"content":[]}')
items = []
names = [('Nordic Blue', '#73cbf1'), ('Pacific Coral', '#eb946e'), ('Alpine Silver', '#b5c4d5'),
         ('Emerald Coast', '#68cbb1'), ('Sunset Orange', '#e5b565'), ('Midnight Violet', '#aea0e8'),
         ('Ocean Teal', '#65c6c9'), ('Arctic White', '#eff3f7')]
for i, (name, color) in enumerate(names):
    path = community / 'pmdg-aircraft-738-liveries' / 'SimObjects' / 'Airplanes' / 'PMDG 737-800' / name
    path.mkdir(parents=True, exist_ok=True)
    im = Image.new('RGB', (840, 380), '#262e3b'); d = ImageDraw.Draw(im)
    for y in range(380):
        v = y / 380
        d.line((0,y,840,y), fill=(int(39-12*v),int(50-14*v),int(66-17*v)))
    d.ellipse((80, 278, 755, 322), fill='#17202b')
    d.polygon([(140,225),(92,82),(140,82),(234,225)], fill=color)
    d.polygon([(360,234),(462,310),(590,315),(446,227)], fill='#a2adbd')
    d.polygon([(335,216),(441,148),(515,153),(424,225)], fill='#8595a9')
    d.rounded_rectangle((106,206,726,258), radius=24, fill='#eaf0f7')
    d.polygon([(692,208),(750,230),(737,250),(689,256)], fill='#eaf0f7')
    d.rectangle((133,233,708,245), fill=color)
    d.polygon([(696,213),(720,221),(720,229),(691,223)], fill='#253750')
    for x in range(240,674,18): d.rounded_rectangle((x,215,x+7,223),radius=3,fill='#39506a')
    d.rounded_rectangle((402,251,456,282),radius=13,fill='#bcc9d8')
    d.ellipse((441,254,460,281),fill='#26394f')
    font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 22)
    d.text((28,22),'737-800  /  DEMO LIVERY',font=font,fill='#b8c7da')
    im.save(path/'thumbnail.png')
    items.append(app.InstalledLivery(package_root=package, aircraft_name='PMDG 737-800', name=name,
        path=path, thumbnail_path=path/'thumbnail.png', file_count=32+i, folder_count=2,
        total_size=(156+i*23)*1024*1024, modified_time=1786406400,
        metadata={'title':name, 'ui_variation':name, 'atc_id':f'DEMO-{i+1:02}', 'atc_airline':'Illustrative demo'}))
window=app.launch_gui(run_mainloop=False, detect_on_start=False)
window.geometry('1360x860+80+40')
window.title(f'PMDG Livery Installer MSFS2024 · v{VERSION} · Demo library')
window.community_var.set(str(community))
label='PMDG 737-800  ·  Demo library'
window.package_paths[label]=package
window.package_var.set(label)
window.package_combo['values']=[label]
window.installed_package_combo['values']=[label]
window.package_count_var.set('1 demo aircraft')
window.installed_liveries=items
window.render_liveries()
window.status_var.set('Demo library  ·  Illustrative thumbnails; no simulator files used')
window.mainloop()

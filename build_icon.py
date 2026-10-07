"""Generate original tail-fin branding and matching navigation icons."""
from pathlib import Path
import math
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / 'assets'

def draw_icon(size=512):
    im = Image.new('RGBA', (512, 512))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((16, 16, 496, 496), radius=110, fill='#171d29', outline='#35465f', width=5)
    # Swept tail fin, painted with two diagonal livery bands.
    shape = [(132, 383), (301, 111), (354, 111), (352, 343), (408, 383)]
    mask = Image.new('L', (512, 512)); ImageDraw.Draw(mask).polygon(shape, fill=255)
    paint = Image.new('RGBA', (512, 512), '#f3f6fc'); pd = ImageDraw.Draw(paint)
    pd.polygon([(91, 324), (407, 203), (407, 269), (91, 390)], fill='#62c8e9')
    pd.polygon([(91, 395), (407, 274), (407, 342), (91, 463)], fill='#377bd6')
    im.paste(paint, (0, 0), mask)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((128, 405, 407, 421), radius=8, fill='#e7a478')
    return im.resize((size, size), Image.Resampling.LANCZOS)

def navigation(name):
    im = Image.new('RGBA', (96, 96)); d = ImageDraw.Draw(im); c = '#bbc8db'; w = 6
    if name == 'grid':
        for x in (15, 55):
            for y in (15, 55): d.rounded_rectangle((x, y, x+26, y+26), radius=5, outline=c, width=w)
    elif name == 'download':
        d.line([(48, 12), (48, 60)], fill=c, width=w)
        d.line([(29, 42), (48, 61), (67, 42)], fill=c, width=w, joint='curve')
        d.line([(17, 60), (17, 81), (79, 81), (79, 60)], fill=c, width=w, joint='curve')
    elif name == 'plane':
        d.polygon([(43, 13), (53, 13), (57, 38), (83, 57), (83, 64), (55, 54), (54, 75), (65, 83), (65, 87), (48, 82), (31, 87), (31, 83), (42, 75), (41, 54), (13, 64), (13, 57), (39, 38)], fill=c)
    elif name == 'check':
        d.rounded_rectangle((13, 15, 83, 82), radius=16, outline=c, width=w)
        d.line([(28, 48), (42, 62), (68, 34)], fill=c, width=w, joint='curve')
    else:
        d.ellipse((25, 25, 71, 71), outline=c, width=w)
        d.ellipse((40, 40, 56, 56), outline=c, width=5)
        for angle in range(0, 360, 45):
            a=math.radians(angle)
            d.line([(48+math.cos(a)*24,48+math.sin(a)*24),(48+math.cos(a)*36,48+math.sin(a)*36)],fill=c,width=9)
    return im.resize((48, 48), Image.Resampling.LANCZOS)

def main():
    OUT.mkdir(exist_ok=True)
    im = draw_icon()
    im.save(OUT/'pmdg_livery_installer_icon.png')
    im.save(OUT/'pmdg_livery_installer_icon.ico', sizes=[(n,n) for n in (16,24,32,48,64,128,256)])
    for name in ('grid','download','plane','check','settings'):
        navigation(name).save(OUT/f'nav_{name}.png')
    print('Generated application and navigation icons.')

if __name__ == '__main__': main()

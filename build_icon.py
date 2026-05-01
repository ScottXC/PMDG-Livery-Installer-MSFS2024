from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "assets"
PNG_PATH = OUT_DIR / "pmdg_livery_installer_icon.png"
ICO_PATH = OUT_DIR / "pmdg_livery_installer_icon.ico"


def scale_points(points: list[tuple[float, float]], size: int) -> list[tuple[int, int]]:
    return [(round(x * size), round(y * size)) for x, y in points]


def draw_icon(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Base tile.
    radius = round(size * 0.18)
    draw.rounded_rectangle(
        (round(size * 0.06), round(size * 0.06), round(size * 0.94), round(size * 0.94)),
        radius=radius,
        fill=(18, 31, 46, 255),
    )

    # Subtle top-left highlight and bottom-right shade.
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rounded_rectangle(
        (round(size * 0.08), round(size * 0.08), round(size * 0.92), round(size * 0.92)),
        radius=round(size * 0.16),
        outline=(255, 255, 255, 30),
        width=max(1, round(size * 0.018)),
    )
    odraw.polygon(
        scale_points([(0.08, 0.08), (0.92, 0.08), (0.08, 0.60)], size),
        fill=(42, 102, 148, 70),
    )
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)

    # Accent stripe, representing a livery paint band.
    draw.rounded_rectangle(
        (round(size * 0.14), round(size * 0.67), round(size * 0.86), round(size * 0.80)),
        radius=round(size * 0.035),
        fill=(32, 178, 170, 255),
    )
    draw.rounded_rectangle(
        (round(size * 0.16), round(size * 0.72), round(size * 0.62), round(size * 0.80)),
        radius=round(size * 0.025),
        fill=(246, 195, 67, 255),
    )

    # Aircraft silhouette.
    plane = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(plane)
    plane_color = (242, 248, 252, 255)
    pdraw.rounded_rectangle(
        (round(size * 0.19), round(size * 0.46), round(size * 0.78), round(size * 0.54)),
        radius=round(size * 0.025),
        fill=plane_color,
    )
    pdraw.polygon(
        scale_points([(0.76, 0.43), (0.90, 0.50), (0.76, 0.57)], size),
        fill=plane_color,
    )
    pdraw.polygon(
        scale_points([(0.40, 0.46), (0.56, 0.23), (0.66, 0.25), (0.55, 0.47)], size),
        fill=plane_color,
    )
    pdraw.polygon(
        scale_points([(0.44, 0.53), (0.62, 0.70), (0.52, 0.75), (0.36, 0.54)], size),
        fill=plane_color,
    )
    pdraw.polygon(
        scale_points([(0.20, 0.46), (0.12, 0.34), (0.22, 0.34), (0.33, 0.47)], size),
        fill=plane_color,
    )
    pdraw.rectangle(
        (round(size * 0.25), round(size * 0.49), round(size * 0.70), round(size * 0.51)),
        fill=(34, 124, 174, 255),
    )

    shadow = plane.filter(ImageFilter.GaussianBlur(max(1, size // 48)))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rectangle((0, 0, size, size), fill=(0, 0, 0, 0))
    shadow = plane.copy().filter(ImageFilter.GaussianBlur(max(1, size // 42)))
    shadow = Image.eval(shadow, lambda value: min(value, 90))
    shifted_shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shifted_shadow.alpha_composite(shadow, (round(size * 0.018), round(size * 0.026)))
    image = Image.alpha_composite(image, shifted_shadow)
    image = Image.alpha_composite(image, plane)

    # Small install arrow.
    draw = ImageDraw.Draw(image)
    arrow_color = (246, 195, 67, 255)
    draw.rounded_rectangle(
        (round(size * 0.66), round(size * 0.20), round(size * 0.74), round(size * 0.40)),
        radius=round(size * 0.018),
        fill=arrow_color,
    )
    draw.polygon(
        scale_points([(0.61, 0.38), (0.79, 0.38), (0.70, 0.52)], size),
        fill=arrow_color,
    )

    return image


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw_icon(size) for size in sizes]
    images[-1].save(PNG_PATH)
    images[-1].save(ICO_PATH, sizes=[(size, size) for size in sizes])
    print(f"Wrote {PNG_PATH}")
    print(f"Wrote {ICO_PATH}")


if __name__ == "__main__":
    main()

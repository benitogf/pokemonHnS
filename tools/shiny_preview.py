#!/usr/bin/env python3
"""
Preview shiny palettes applied to battle sprites and follower icons.

Usage:
  # Preview current shiny palettes for a species:
  python3 tools/shiny_preview.py sandslash

  # Preview with a custom palette file applied to both battle + follower:
  python3 tools/shiny_preview.py sandslash --pal custom.pal

  # Preview with separate battle and follower palettes:
  python3 tools/shiny_preview.py sandslash --battle-pal battle.pal --follower-pal follower.pal

  # Preview multiple palette options side by side:
  python3 tools/shiny_preview.py sandslash --pal optA.pal optB.pal optC.pal

  # Extract dominant colors from a reference image:
  python3 tools/shiny_preview.py sandslash --ref-image photo.png --num-colors 6

Output is saved to /tmp/shiny_preview_<species>.png
"""

import argparse
import os
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("PIL not found. Install with: pip install Pillow")


def parse_pal(path):
    """Parse a JASC .pal file into a flat RGB list (16 colors = 48 entries)."""
    with open(path) as f:
        lines = f.read().strip().split("\n")
    colors = []
    for line in lines[3:19]:
        r, g, b = line.strip().split()
        colors.extend([int(r), int(g), int(b)])
    return colors


def pal_to_tuples(pal):
    """Convert flat RGB list to list of (R,G,B) tuples."""
    return [(pal[i], pal[i + 1], pal[i + 2]) for i in range(0, len(pal), 3)]


def tuples_to_flat(tuples):
    """Convert list of (R,G,B) tuples to flat RGB list."""
    flat = []
    for r, g, b in tuples:
        flat.extend([r, g, b])
    return flat


def make_transparent(img):
    """Convert indexed image to RGBA with magenta (255,0,255) as transparent."""
    img = img.convert("RGBA")
    pixels = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = pixels[x, y]
            if r == 255 and g == 0 and b == 255:
                pixels[x, y] = (0, 0, 0, 0)
    return img


def apply_pal(img, pal_data):
    """Apply a palette to an indexed image, return RGBA with transparency."""
    p = img.copy()
    orig = list(img.getpalette())
    orig[: len(pal_data)] = pal_data
    p.putpalette(orig)
    return make_transparent(p)


def extract_colors(image_path, num_colors=8):
    """Extract dominant colors from a reference image using quantization."""
    img = Image.open(image_path).convert("RGB")
    small = img.resize((150, 150), Image.LANCZOS)
    quantized = small.quantize(colors=num_colors, method=Image.Quantize.MEDIANCUT)
    pal = quantized.getpalette()
    colors = [(pal[i * 3], pal[i * 3 + 1], pal[i * 3 + 2]) for i in range(num_colors)]
    # Sort by luminance
    colors.sort(key=lambda c: c[0] * 0.299 + c[1] * 0.587 + c[2] * 0.114, reverse=True)
    return colors


def find_species_paths(species):
    """Find all relevant file paths for a species."""
    species_lower = species.lower()
    base = Path("graphics/pokemon") / species_lower
    follower = Path("graphics/object_events/pics/pokemon") / f"{species_lower}.png"

    paths = {
        "front": base / "front.png",
        "back": base / "back.png",
        "normal_pal": base / "normal.pal",
        "shiny_pal": base / "shiny.pal",
        "follower_shiny_pal": base / "follower_shiny.pal",
        "follower_sprite": follower,
    }
    return paths


def render_preview(species, palette_options, labels=None, scale=6, output=None):
    """
    Render a preview image with battle sprites and follower icon.

    palette_options: list of dicts, each with:
      - 'battle_pal': flat RGB list for battle sprites
      - 'follower_pal': flat RGB list for follower sprite
      - 'label': str label
    """
    paths = find_species_paths(species)

    # Load sprites
    front = Image.open(paths["front"])
    back = Image.open(paths["back"])

    has_follower = paths["follower_sprite"].exists()
    if has_follower:
        follower_sheet = Image.open(paths["follower_sprite"])
        follower = follower_sheet.crop((0, 0, 32, 32))

    n = len(palette_options)
    spacing = 16
    label_h = 24

    # Render battle front/back for each option
    battle_rows = []
    follower_imgs = []

    for opt in palette_options:
        bf = apply_pal(front, opt["battle_pal"]).resize(
            (front.width * scale, front.height * scale), Image.NEAREST
        )
        bb = apply_pal(back, opt["battle_pal"]).resize(
            (back.width * scale, back.height * scale), Image.NEAREST
        )
        battle_rows.append((bf, bb))

        if has_follower:
            fw = 32 * scale
            fi = apply_pal(follower, opt["follower_pal"]).resize((fw, fw), Image.NEAREST)
            follower_imgs.append(fi)

    # Layout: each option is a column with front, back, follower stacked
    bw = front.width * scale
    bh = front.height * scale
    fw = 32 * scale if has_follower else 0
    fh = fw

    col_w = max(bw, fw)
    col_inner_h = label_h + bh + spacing + bh + (spacing + fh if has_follower else 0)
    total_w = col_w * n + spacing * (n - 1)
    total_h = col_inner_h

    bg = (40, 40, 40, 255)
    out = Image.new("RGBA", (total_w, total_h), bg)
    draw = ImageDraw.Draw(out)

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16
        )
    except Exception:
        font = ImageFont.load_default()

    for i, opt in enumerate(palette_options):
        x = i * (col_w + spacing)
        y = 0
        label = opt.get("label", chr(65 + i))

        # Label
        draw.text((x + col_w // 2 - 8, y + 2), label, fill="white", font=font)
        y += label_h

        # Front
        bf, bb = battle_rows[i]
        cx = x + (col_w - bw) // 2
        out.paste(bf, (cx, y), bf)
        y += bh + spacing

        # Back
        out.paste(bb, (cx, y), bb)
        y += bh + spacing

        # Follower
        if has_follower and i < len(follower_imgs):
            fx = x + (col_w - fw) // 2
            out.paste(follower_imgs[i], (fx, y), follower_imgs[i])

    if output is None:
        output = f"/tmp/shiny_preview_{species}.png"
    out.save(output)
    print(f"Saved {output} ({out.size[0]}x{out.size[1]})")
    return output


def main():
    parser = argparse.ArgumentParser(description="Preview shiny palettes on sprites")
    parser.add_argument("species", help="Species name (e.g. sandslash)")
    parser.add_argument("--pal", nargs="+", help="Custom .pal file(s) applied to both battle+follower")
    parser.add_argument("--battle-pal", help="Custom battle .pal file")
    parser.add_argument("--follower-pal", help="Custom follower .pal file")
    parser.add_argument("--ref-image", help="Reference image to extract colors from")
    parser.add_argument("--num-colors", type=int, default=8, help="Colors to extract from ref image")
    parser.add_argument("--scale", type=int, default=6, help="Pixel scale factor")
    parser.add_argument("-o", "--output", help="Output path (default: /tmp/shiny_preview_<species>.png)")
    args = parser.parse_args()

    paths = find_species_paths(args.species)

    if args.ref_image:
        colors = extract_colors(args.ref_image, args.num_colors)
        print("Extracted colors (sorted by luminance, brightest first):")
        for i, (r, g, b) in enumerate(colors):
            print(f"  [{i}] RGB({r}, {g}, {b})")

    if args.pal:
        options = []
        for i, p in enumerate(args.pal):
            pal = parse_pal(p)
            options.append({
                "battle_pal": pal,
                "follower_pal": pal,
                "label": chr(65 + i),
            })
        render_preview(args.species, options, scale=args.scale, output=args.output)
    elif args.battle_pal or args.follower_pal:
        bp = parse_pal(args.battle_pal) if args.battle_pal else parse_pal(paths["shiny_pal"])
        fp = parse_pal(args.follower_pal) if args.follower_pal else parse_pal(paths["follower_shiny_pal"])
        render_preview(args.species, [{"battle_pal": bp, "follower_pal": fp, "label": "Custom"}],
                       scale=args.scale, output=args.output)
    else:
        # Default: show current shiny palette
        bp = parse_pal(paths["shiny_pal"])
        fp = parse_pal(paths["follower_shiny_pal"]) if paths["follower_shiny_pal"].exists() else bp
        render_preview(args.species, [{"battle_pal": bp, "follower_pal": fp, "label": "Current"}],
                       scale=args.scale, output=args.output)


if __name__ == "__main__":
    main()

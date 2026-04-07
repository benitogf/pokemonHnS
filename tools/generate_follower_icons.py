#!/usr/bin/env python3
"""
Generate follower_icon.png files from overworld follower spritesheets.

Extracts the first two 32x32 frames from each species' follower PNG and
stacks them into a 32x64 indexed-color PNG. The build system converts
these to .4bpp automatically via the %.4bpp: %.png rule.

These are used as shiny icon tile data, since follower sprites use the
species' normal.pal indices and can be palette-swapped to shiny.pal.

For oversized followers (64x64 frames), only 32x32 is supported as icon
so those species are skipped.

Also generates src/data/pokemon_graphics/follower_icon_table.h with INCBIN
entries and a lookup table.

Usage: python3 tools/generate_follower_icons.py
"""

import os
import glob
import re
import sys

try:
    from PIL import Image
except ImportError:
    print("Error: Pillow is required. Install with: pip3 install Pillow", file=sys.stderr)
    sys.exit(1)


# Max frame size for standard 32x32 followers (192x32 = 6 frames or 256x32 = 8 frames)
MAX_PNG_WIDTH = 256
FRAME_SIZE = 32

# Directories in graphics/pokemon/ that are not real species (no SPECIES_XXX constant)
NON_SPECIES_DIRS = {"question_mark"}


def to_c_name(species_name):
    """Convert species directory name to C identifier (e.g. 'nidoran_f' -> 'NidoranF')."""
    return "".join(word.capitalize() for word in species_name.split("_"))


def to_species_constant(species_name):
    """Convert species directory name to SPECIES_XXX constant."""
    return "SPECIES_" + species_name.upper()


def content_center_y(frame):
    """Return the vertical center of non-background pixels in a 32x32 indexed frame."""
    first_row = FRAME_SIZE
    last_row = 0
    for y in range(FRAME_SIZE):
        for x in range(FRAME_SIZE):
            if frame.getpixel((x, y)) != 0:
                first_row = min(first_row, y)
                last_row = max(last_row, y)
                break
    if first_row > last_row:
        return FRAME_SIZE // 2
    return (first_row + last_row) / 2.0


def calc_vertical_shift(follower_frame, species_dir):
    """Calculate pixels to shift follower frame to align with standard icon center."""
    icon_path = os.path.join(species_dir, "icon.png")
    if not os.path.exists(icon_path):
        return 0
    try:
        icon = Image.open(icon_path)
        icon_frame = icon.crop((0, 0, FRAME_SIZE, FRAME_SIZE))
    except Exception:
        return 0
    icon_center = content_center_y(icon_frame)
    follower_center = content_center_y(follower_frame)
    return round(icon_center - follower_center)


def shift_frame(frame, dy, palette):
    """Shift an indexed 32x32 frame by dy pixels vertically (negative = up)."""
    shifted = Image.new("P", (FRAME_SIZE, FRAME_SIZE))
    shifted.putpalette(palette)
    # Paste the frame at the offset position; pixels outside the frame stay as index 0 (bg)
    shifted.paste(frame, (0, dy))
    return shifted


def main():
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))

    # Collect follower sprite PNGs from both directories
    followers = {}
    for f in glob.glob("graphics/object_events/pics/pokemon/*.png"):
        name = os.path.basename(f).replace(".png", "")
        followers[name] = f
    for f in glob.glob("graphics/object_events/pics/pokemon/followers/*.png"):
        name = os.path.basename(f).replace(".png", "")
        followers[name] = f

    # Find species directories that have an icon.png (these are valid species)
    species_dirs = {}
    for d in glob.glob("graphics/pokemon/*/"):
        name = os.path.basename(d.rstrip("/"))
        if os.path.exists(os.path.join(d, "icon.png")):
            species_dirs[name] = d

    generated = []
    skipped_no_follower = []
    skipped_oversized = []
    null_entries = []

    for species_name in sorted(species_dirs):
        if species_name in NON_SPECIES_DIRS:
            continue

        if species_name not in followers:
            skipped_no_follower.append(species_name)
            null_entries.append(species_name)
            continue

        follower_path = followers[species_name]

        try:
            img = Image.open(follower_path)
        except Exception as e:
            print(f"Warning: Could not open {follower_path}: {e}", file=sys.stderr)
            skipped_no_follower.append(species_name)
            null_entries.append(species_name)
            continue

        w, h = img.size

        # Skip oversized sprites (64x64 frames) - they don't fit 32x32 icon format
        if h > FRAME_SIZE or w > MAX_PNG_WIDTH:
            skipped_oversized.append(species_name)
            null_entries.append(species_name)
            continue

        if w < FRAME_SIZE * 2:
            # Need at least 2 frames
            skipped_no_follower.append(species_name)
            null_entries.append(species_name)
            continue

        # Extract frames 0 and 1 (first two 32x32 areas from left)
        frame0 = img.crop((0, 0, FRAME_SIZE, FRAME_SIZE))
        frame1 = img.crop((FRAME_SIZE, 0, FRAME_SIZE * 2, FRAME_SIZE))

        # Calculate vertical shift to align with standard icon center
        y_shift = calc_vertical_shift(frame0, species_dirs[species_name])

        if y_shift != 0:
            frame0 = shift_frame(frame0, y_shift, img.getpalette())
            frame1 = shift_frame(frame1, y_shift, img.getpalette())

        # Create 32x64 indexed-color PNG (frame 0 on top, frame 1 below)
        result = Image.new("P", (FRAME_SIZE, FRAME_SIZE * 2))
        result.putpalette(img.getpalette())
        result.paste(frame0, (0, 0))
        result.paste(frame1, (0, FRAME_SIZE))

        out_path = os.path.join(species_dirs[species_name], "follower_icon.png")
        result.save(out_path)
        generated.append(species_name)

    # Generate C header with INCBIN entries and lookup table
    generate_c_header(generated, null_entries)

    print(f"Generated: {len(generated)} follower_icon.png files")
    if skipped_no_follower:
        print(f"Skipped (no follower): {skipped_no_follower}")
    if skipped_oversized:
        print(f"Skipped (oversized): {skipped_oversized}")

    return 0


def generate_c_header(generated, null_entries):
    """Generate follower_icon_table.h with INCBIN entries and lookup table."""
    lines = [
        "// Auto-generated by tools/generate_follower_icons.py",
        "// Follower sprite frames 0+1 as icon tile data for shiny display",
        "// These use the species' normal.pal indices, enabling palette swap to shiny.pal",
        "",
    ]

    # INCBIN declarations
    for name in generated:
        c_name = to_c_name(name)
        lines.append(
            f'static const u8 sFollowerIcon_{c_name}[] = '
            f'INCBIN_U8("graphics/pokemon/{name}/follower_icon.4bpp");'
        )

    lines.append("")
    lines.append("static const u8 *const sFollowerIconTable[] =")
    lines.append("{")

    # Table entries for species with follower icons
    for name in generated:
        c_name = to_c_name(name)
        constant = to_species_constant(name)
        lines.append(f"    [{constant}] = sFollowerIcon_{c_name},")

    # NULL entries for species without follower icons
    for name in null_entries:
        constant = to_species_constant(name)
        lines.append(f"    [{constant}] = NULL,")

    lines.append("};")
    lines.append("")

    header_path = "src/data/pokemon_graphics/follower_icon_table.h"
    os.makedirs(os.path.dirname(header_path), exist_ok=True)
    with open(header_path, "w") as f:
        f.write("\n".join(lines))

    print(f"Generated header: {header_path} ({len(generated)} with data, {len(null_entries)} NULL, {len(generated) + len(null_entries)} total)")


if __name__ == "__main__":
    sys.exit(main())

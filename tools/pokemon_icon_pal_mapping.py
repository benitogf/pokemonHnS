#!/usr/bin/env python3
"""Generate per-species icon-to-sprite palette mapping tables.

For each Pokemon species, maps each icon palette color to the nearest
color in the species' normal battle sprite palette. This precomputed
mapping lets the runtime construct shiny icon palettes by looking up
the corresponding shiny palette color, producing accurate shiny icons.

Usage: python3 tools/pokemon_icon_pal_mapping.py
Output: src/data/pokemon_graphics/icon_pal_mapping.h
"""

import os
import re
import sys

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow required. Install: pip3 install Pillow", file=sys.stderr)
    sys.exit(1)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Squared Euclidean distance threshold in 8-bit RGB space.
# Colors above this threshold get 0xFF (keep original icon color).
DISTANCE_THRESHOLD = 12000


def read_jasc_pal(path):
    """Read a JASC-PAL file, return list of (R,G,B) tuples (max 16)."""
    colors = []
    with open(path) as f:
        lines = f.readlines()
    for line in lines[3:]:
        parts = line.strip().split()
        if len(parts) == 3:
            colors.append((int(parts[0]), int(parts[1]), int(parts[2])))
    return colors[:16]


def color_distance(c1, c2):
    """Perceptual color distance using weighted Euclidean with redmean."""
    r1, g1, b1 = c1
    r2, g2, b2 = c2
    rmean = (r1 + r2) // 2
    dr, dg, db = r1 - r2, g1 - g2, b1 - b2
    # Redmean formula weights channels by human perception
    return (2 + rmean // 256) * dr * dr + 4 * dg * dg + (2 + (255 - rmean) // 256) * db * db


def get_icon_info(icon_path):
    """Read icon.png, return (set of used palette indices, palette colors)."""
    img = Image.open(icon_path)
    if img.mode != 'P':
        return set(), []
    pixels = list(img.getdata())
    pal_raw = img.getpalette()
    palette = [(pal_raw[i * 3], pal_raw[i * 3 + 1], pal_raw[i * 3 + 2]) for i in range(16)]
    return set(pixels), palette


def parse_species_ids(path):
    """Parse species.h -> {name: id} for simple numeric defines."""
    species = {}
    with open(path) as f:
        for line in f:
            m = re.match(r'#define\s+(SPECIES_\w+)\s+(\d+)\s*$', line)
            if m:
                species[m.group(1)] = int(m.group(2))
    return species


def parse_icon_paths(path):
    """Parse pokemon.h INCBIN lines -> {symbol: directory_path}."""
    icons = {}
    with open(path) as f:
        for line in f:
            m = re.match(r'const u8 (gMonIcon_\w+)\[\].*INCBIN_U8\("(graphics/pokemon/\w+)/icon\.4bpp"\)', line)
            if m:
                icons[m.group(1)] = m.group(2)
    return icons


def parse_icon_table(path):
    """Parse gMonIconTable -> {SPECIES_X: gMonIcon_Y}."""
    table = {}
    in_table = False
    with open(path) as f:
        for line in f:
            if 'gMonIconTable[]' in line:
                in_table = True
                continue
            if in_table:
                if '};' in line:
                    break
                m = re.match(r'\s*\[(SPECIES_\w+)\]\s*=\s*(gMonIcon_\w+)', line)
                if m:
                    table[m.group(1)] = m.group(2)
    return table


def parse_palette_indices(path):
    """Parse gMonIconPaletteIndices -> {SPECIES_X: palette_index}."""
    indices = {}
    in_array = False
    with open(path) as f:
        for line in f:
            if 'gMonIconPaletteIndices[]' in line:
                in_array = True
                continue
            if in_array:
                if '};' in line:
                    break
                m = re.match(r'\s*\[(SPECIES_\w+)\]\s*=\s*(\d+)', line)
                if m:
                    indices[m.group(1)] = int(m.group(2))
    return indices


def main():
    species_h = os.path.join(PROJECT_ROOT, 'include/constants/species.h')
    pokemon_h = os.path.join(PROJECT_ROOT, 'src/data/graphics/pokemon.h')
    pokemon_icon_c = os.path.join(PROJECT_ROOT, 'src/pokemon_icon.c')
    icon_pal_dir = os.path.join(PROJECT_ROOT, 'graphics/pokemon/icon_palettes')
    output_path = os.path.join(PROJECT_ROOT, 'src/data/pokemon_graphics/icon_pal_mapping.h')

    # Read shared icon palettes
    icon_pals = []
    for i in range(3):
        icon_pals.append(read_jasc_pal(os.path.join(icon_pal_dir, f'icon_palette_{i}.pal')))

    # Parse source data
    species_ids = parse_species_ids(species_h)
    icon_paths = parse_icon_paths(pokemon_h)
    icon_table = parse_icon_table(pokemon_icon_c)
    pal_indices = parse_palette_indices(pokemon_icon_c)

    num_species = species_ids.get('SPECIES_EGG', 462)

    # Build species -> directory mapping via icon_table -> icon_paths
    species_dirs = {}
    for sp_name, symbol in icon_table.items():
        if symbol in icon_paths:
            species_dirs[sp_name] = icon_paths[symbol]

    # Process each species
    mappings = {}
    warnings = []
    stats = {'good': 0, 'poor': 0, 'unmapped': 0}

    for sp_name in sorted(species_dirs.keys(), key=lambda x: species_ids.get(x, 9999)):
        sp_id = species_ids.get(sp_name, 0)
        if sp_id == 0 or sp_id >= num_species:
            continue
        if sp_name not in pal_indices:
            continue

        gfx_dir = os.path.join(PROJECT_ROOT, species_dirs[sp_name])
        icon_path = os.path.join(gfx_dir, 'icon.png')
        normal_pal_path = os.path.join(gfx_dir, 'normal.pal')

        if not os.path.exists(icon_path) or not os.path.exists(normal_pal_path):
            continue

        pal_idx = pal_indices[sp_name]
        if pal_idx >= len(icon_pals):
            continue
        icon_palette = icon_pals[pal_idx]
        normal_pal = read_jasc_pal(normal_pal_path)

        used_indices, _png_pal = get_icon_info(icon_path)

        # Compute mapping
        mapping = [0xFF] * 16
        mapping[0] = 0  # Transparency

        for i in range(1, 16):
            if i not in used_indices:
                continue

            icon_color = icon_palette[i]

            best_dist = 999999
            best_idx = 0
            for j in range(16):
                d = color_distance(icon_color, normal_pal[j])
                if d < best_dist:
                    best_dist = d
                    best_idx = j

            if best_dist <= DISTANCE_THRESHOLD:
                mapping[i] = best_idx
                stats['good'] += 1
            else:
                mapping[i] = 0xFF
                stats['poor'] += 1
                warnings.append(
                    f"  {sp_name}[{i}]: icon{icon_color} best=normal[{best_idx}]{normal_pal[best_idx]} dist={best_dist}"
                )

        mappings[sp_name] = mapping

    # Print summary
    print(f"Processed {len(mappings)} species")
    print(f"  Good matches: {stats['good']}")
    print(f"  Poor matches (kept original): {stats['poor']}")
    if warnings:
        print(f"\nPoor matches (above threshold {DISTANCE_THRESHOLD}):")
        for w in warnings[:40]:
            print(w)
        if len(warnings) > 40:
            print(f"  ... and {len(warnings) - 40} more")

    # Write C header
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        f.write("// Auto-generated by tools/pokemon_icon_pal_mapping.py\n")
        f.write("// Maps icon palette indices to normal.pal indices for shiny icon palettes.\n")
        f.write("// 0xFF = no good mapping, keep original icon palette color.\n")
        f.write("// Regenerate with: python3 tools/pokemon_icon_pal_mapping.py\n\n")

        f.write("static const u8 sIconToSpritePalMap[NUM_SPECIES + 1][16] =\n{\n")

        # Write in species ID order
        for sp_name, sp_id in sorted(species_ids.items(), key=lambda x: x[1]):
            if sp_name in mappings:
                m = mappings[sp_name]
                vals = ', '.join(f'0x{v:02X}' for v in m)
                f.write(f"    [{sp_name}] = {{ {vals} }},\n")

        f.write("};\n")

    print(f"\nWrote {output_path}")


if __name__ == '__main__':
    main()

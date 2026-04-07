#!/usr/bin/env python3
"""Generate precomputed shiny icon palettes via HSV color-shift.

For each Pokemon species, computes the dominant color transformation
between normal and shiny battle sprite palettes in HSV space, then
applies that same transformation to the icon palette colors. This
produces a natural-looking shiny icon that preserves icon art quality.

Usage: python3 tools/pokemon_icon_pal_mapping.py
Output: src/data/pokemon_graphics/icon_pal_mapping.h
"""

import colorsys
import math
import os
import re
import sys

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow required. Install: pip3 install Pillow", file=sys.stderr)
    sys.exit(1)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Species with modern shiny variants (must match SpeciesHasModernShiny in C)
MODERN_SHINY_SPECIES = [
    'SPECIES_PIKACHU', 'SPECIES_RAICHU', 'SPECIES_PICHU',
    'SPECIES_VAPOREON', 'SPECIES_JOLTEON', 'SPECIES_FLAREON',
    'SPECIES_REGICE', 'SPECIES_HERACROSS', 'SPECIES_HAUNTER',
    'SPECIES_GENGAR', 'SPECIES_SCYTHER', 'SPECIES_BLAZIKEN',
    'SPECIES_XATU', 'SPECIES_PARAS', 'SPECIES_CHINCHOU',
    'SPECIES_LANTURN', 'SPECIES_ZAPDOS', 'SPECIES_ELEKID',
    'SPECIES_FARFETCHD', 'SPECIES_MAROWAK', 'SPECIES_PHANPY',
    'SPECIES_LAPRAS', 'SPECIES_TENTACOOL', 'SPECIES_TENTACRUEL',
]


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


def rgb_to_hsv(r, g, b):
    """Convert 8-bit RGB to HSV (h=0-360, s=0-1, v=0-1)."""
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return h * 360.0, s, v


def hsv_to_rgb(h, s, v):
    """Convert HSV to 8-bit RGB."""
    h = h % 360.0
    s = max(0.0, min(1.0, s))
    v = max(0.0, min(1.0, v))
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def rgb_to_gba(r, g, b):
    """Convert 8-bit RGB to 15-bit GBA color (5 bits per channel)."""
    r5 = (r >> 3) & 0x1F
    g5 = (g >> 3) & 0x1F
    b5 = (b >> 3) & 0x1F
    return r5 | (g5 << 5) | (b5 << 10)


def is_grey(r, g, b, threshold=20):
    """Check if a color is greyscale (low saturation)."""
    return max(r, g, b) - min(r, g, b) < threshold


def compute_palette_shift(normal_pal, shiny_pal):
    """Compute the dominant HSV shift between normal and shiny palettes.

    Returns (delta_hue, sat_ratio, val_ratio) computed from the most
    chromatic (non-grey) colors that actually change between palettes.
    """
    hue_shifts = []
    sat_ratios = []
    val_ratios = []

    for i in range(1, 16):  # Skip transparency at 0
        nr, ng, nb = normal_pal[i]
        sr, sg, sb = shiny_pal[i]

        # Skip identical colors
        if (nr, ng, nb) == (sr, sg, sb):
            continue

        nh, ns, nv = rgb_to_hsv(nr, ng, nb)
        sh, ss, sv = rgb_to_hsv(sr, sg, sb)

        # Weight by saturation - chromatic colors matter more
        weight = ns
        if weight < 0.08:
            # Very grey normal color - still compute val shift
            if nv > 0.05:
                val_ratios.append((sv / nv if nv > 0 else 1.0, 0.3))
            continue

        # Hue shift (handle circular wraparound)
        dh = sh - nh
        if dh > 180:
            dh -= 360
        elif dh < -180:
            dh += 360

        hue_shifts.append((dh, weight))
        sat_ratios.append((ss / ns if ns > 0 else 1.0, weight))
        val_ratios.append((sv / nv if nv > 0 else 1.0, weight))

    # Weighted averages
    def wavg(pairs, default):
        total_w = sum(w for _, w in pairs)
        if total_w < 0.01:
            return default
        return sum(v * w for v, w in pairs) / total_w

    dh = wavg(hue_shifts, 0.0)
    sr = wavg(sat_ratios, 1.0)
    vr = wavg(val_ratios, 1.0)

    return dh, sr, vr


def apply_shift(color, dh, sr, vr, is_used):
    """Apply HSV shift to a single RGB color. Returns shifted (R,G,B)."""
    r, g, b = color
    if not is_used:
        return color  # Don't shift unused palette entries

    h, s, v = rgb_to_hsv(r, g, b)

    # For very dark or very light colors, only adjust value
    if s < 0.08:
        new_v = min(1.0, v * vr)
        return hsv_to_rgb(h, s, new_v)

    new_h = (h + dh) % 360.0
    new_s = max(0.0, min(1.0, s * sr))
    new_v = max(0.0, min(1.0, v * vr))

    return hsv_to_rgb(new_h, new_s, new_v)


def get_used_indices(icon_path):
    """Read icon.png, return set of used palette indices."""
    img = Image.open(icon_path)
    if img.mode != 'P':
        return set()
    pixels = img.tobytes()
    return set(pixels)


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
    std_palettes = {}   # species_name -> [16 GBA u16 colors]
    mod_palettes = {}   # species_name -> [16 GBA u16 colors] (modern shiny)
    stats = {'processed': 0, 'modern': 0, 'no_change': 0}

    for sp_name in sorted(species_dirs.keys(), key=lambda x: species_ids.get(x, 9999)):
        sp_id = species_ids.get(sp_name, 0)
        if sp_id == 0 or sp_id >= num_species:
            continue
        if sp_name not in pal_indices:
            continue

        gfx_dir = os.path.join(PROJECT_ROOT, species_dirs[sp_name])
        icon_path = os.path.join(gfx_dir, 'icon.png')
        normal_pal_path = os.path.join(gfx_dir, 'normal.pal')
        shiny_pal_path = os.path.join(gfx_dir, 'shiny.pal')

        if not os.path.exists(icon_path) or not os.path.exists(normal_pal_path):
            continue
        if not os.path.exists(shiny_pal_path):
            continue

        pal_idx = pal_indices[sp_name]
        if pal_idx >= len(icon_pals):
            continue

        icon_palette = icon_pals[pal_idx]
        normal_pal = read_jasc_pal(normal_pal_path)
        shiny_pal = read_jasc_pal(shiny_pal_path)
        used_indices = get_used_indices(icon_path)

        # Compute standard shiny shift and apply to icon palette
        dh, sr, vr = compute_palette_shift(normal_pal, shiny_pal)

        gba_colors = []
        for i in range(16):
            shifted = apply_shift(icon_palette[i], dh, sr, vr, i in used_indices and i > 0)
            gba_colors.append(rgb_to_gba(*shifted))

        # Keep transparency color unchanged
        gba_colors[0] = rgb_to_gba(*icon_palette[0])

        std_palettes[sp_name] = gba_colors
        stats['processed'] += 1

        # Check if normal and shiny are identical (no shift)
        if all(std_palettes[sp_name][i] == rgb_to_gba(*icon_palette[i]) for i in range(16)):
            stats['no_change'] += 1

        # Modern shiny variant
        if sp_name in MODERN_SHINY_SPECIES:
            mod_shiny_path = os.path.join(gfx_dir, 'shiny_modern.pal')
            if os.path.exists(mod_shiny_path):
                mod_shiny_pal = read_jasc_pal(mod_shiny_path)
                dh_m, sr_m, vr_m = compute_palette_shift(normal_pal, mod_shiny_pal)

                mod_gba = []
                for i in range(16):
                    shifted = apply_shift(icon_palette[i], dh_m, sr_m, vr_m, i in used_indices and i > 0)
                    mod_gba.append(rgb_to_gba(*shifted))
                mod_gba[0] = rgb_to_gba(*icon_palette[0])

                mod_palettes[sp_name] = mod_gba
                stats['modern'] += 1

    # Print summary
    print(f"Processed {stats['processed']} species")
    print(f"  Modern shiny variants: {stats['modern']}")
    print(f"  No visible change: {stats['no_change']}")

    # Show sample transformations
    for sample in ['SPECIES_GENGAR', 'SPECIES_SANDSLASH', 'SPECIES_PIKACHU']:
        if sample in std_palettes and sample in species_dirs:
            gfx_dir = os.path.join(PROJECT_ROOT, species_dirs[sample])
            normal = read_jasc_pal(os.path.join(gfx_dir, 'normal.pal'))
            shiny = read_jasc_pal(os.path.join(gfx_dir, 'shiny.pal'))
            pal_idx = pal_indices[sample]
            icon_pal = icon_pals[pal_idx]
            used = get_used_indices(os.path.join(gfx_dir, 'icon.png'))
            dh, sr, vr = compute_palette_shift(normal, shiny)
            print(f"\n  {sample}: dH={dh:+.1f}° sR={sr:.2f} vR={vr:.2f}")
            for i in sorted(used):
                if i == 0:
                    continue
                orig = icon_pal[i]
                shifted = apply_shift(orig, dh, sr, vr, True)
                print(f"    [{i:2d}] {orig} -> {shifted}")
            if sample in mod_palettes:
                mod_shiny = read_jasc_pal(os.path.join(gfx_dir, 'shiny_modern.pal'))
                dh_m, sr_m, vr_m = compute_palette_shift(normal, mod_shiny)
                print(f"    Modern: dH={dh_m:+.1f}° sR={sr_m:.2f} vR={vr_m:.2f}")

    # Write C header
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        f.write("// Auto-generated by tools/pokemon_icon_pal_mapping.py\n")
        f.write("// Precomputed shiny icon palettes using HSV color-shift.\n")
        f.write("// Regenerate with: python3 tools/pokemon_icon_pal_mapping.py\n\n")

        f.write("static const u16 sShinyIconPalettes[NUM_SPECIES + 1][16] =\n{\n")
        for sp_name, sp_id in sorted(species_ids.items(), key=lambda x: x[1]):
            if sp_name in std_palettes:
                vals = ', '.join(f'0x{v:04X}' for v in std_palettes[sp_name])
                f.write(f"    [{sp_name}] = {{ {vals} }},\n")
        f.write("};\n\n")

        # Modern shiny palettes - separate table indexed by species ID
        f.write("static const u16 sShinyModernIconPalettes[NUM_SPECIES + 1][16] =\n{\n")
        for sp_name, sp_id in sorted(species_ids.items(), key=lambda x: x[1]):
            if sp_name in mod_palettes:
                vals = ', '.join(f'0x{v:04X}' for v in mod_palettes[sp_name])
                f.write(f"    [{sp_name}] = {{ {vals} }},\n")
        f.write("};\n")

    print(f"\nWrote {output_path}")


if __name__ == '__main__':
    main()

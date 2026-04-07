#!/usr/bin/env python3
"""Generate precomputed shiny icon palettes via per-color HSV shifts.

For each Pokemon species, for each icon palette color:
1. Find the nearest normal.pal battle sprite color
2. Compute the HSV shift from that normal.pal color to the corresponding
   shiny.pal color
3. Apply that per-color shift to the icon color

This preserves icon visual quality while accurately reflecting each
color's individual shiny transformation.

Usage: python3 tools/pokemon_icon_pal_mapping.py
Output: src/data/pokemon_graphics/icon_pal_mapping.h
"""

import colorsys
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
    """Convert HSV to 8-bit RGB, clamped."""
    h = h % 360.0
    s = max(0.0, min(1.0, s))
    v = max(0.0, min(1.0, v))
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def rgb_to_gba(r, g, b):
    """Convert 8-bit RGB to 15-bit GBA color."""
    return ((r >> 3) & 0x1F) | (((g >> 3) & 0x1F) << 5) | (((b >> 3) & 0x1F) << 10)


def color_distance(c1, c2):
    """Redmean perceptual color distance."""
    r1, g1, b1 = c1
    r2, g2, b2 = c2
    rmean = (r1 + r2) // 2
    dr, dg, db = r1 - r2, g1 - g2, b1 - b2
    return (2 + rmean // 256) * dr * dr + 4 * dg * dg + (2 + (255 - rmean) // 256) * db * db


def find_nearest(color, palette):
    """Find index of nearest color in palette using perceptual distance."""
    best_idx = 0
    best_dist = 999999
    for j in range(len(palette)):
        d = color_distance(color, palette[j])
        if d < best_dist:
            best_dist = d
            best_idx = j
    return best_idx, best_dist


def apply_per_color_shift(icon_color, normal_color, shiny_color):
    """Compute HSV shift from normal->shiny and apply to icon_color.

    This transforms the icon color by the same relative change that
    the battle sprite color undergoes for the shiny variant.
    """
    nr, ng, nb = normal_color
    sr, sg, sb = shiny_color
    ir, ig, ib = icon_color

    # If normal and shiny are identical, no change needed
    if (nr, ng, nb) == (sr, sg, sb):
        return icon_color

    nh, ns, nv = rgb_to_hsv(nr, ng, nb)
    sh, ss, sv = rgb_to_hsv(sr, sg, sb)
    ih, is_, iv = rgb_to_hsv(ir, ig, ib)

    # Hue: additive shift (handle wraparound)
    dh = sh - nh
    if dh > 180:
        dh -= 360
    elif dh < -180:
        dh += 360

    # Saturation and value: multiplicative ratio
    if ns > 0.01:
        s_ratio = ss / ns
    else:
        # Normal is grey, shiny has color: use absolute saturation
        s_ratio = 1.0
        is_ = ss  # Direct set

    if nv > 0.01:
        v_ratio = sv / nv
    else:
        v_ratio = 1.0

    # Apply shift to icon color
    new_h = (ih + dh) % 360.0
    if ns > 0.01:
        new_s = max(0.0, min(1.0, is_ * s_ratio))
    else:
        new_s = is_
    new_v = max(0.0, min(1.0, iv * v_ratio))

    # For very grey icon colors (low saturation), skip hue shift
    # but still apply value change
    if is_ < 0.05:
        new_h = ih
        new_s = max(0.0, min(1.0, is_ + (ss - ns)))  # Additive for near-zero

    return hsv_to_rgb(new_h, new_s, new_v)


def compute_shiny_icon_palette(icon_palette, normal_pal, shiny_pal, used_indices):
    """Build a shiny icon palette using per-color HSV shifts."""
    result = []
    for i in range(16):
        if i == 0 or i not in used_indices:
            result.append(icon_palette[i])
            continue

        # Find nearest normal.pal color for this icon color
        best_idx, _ = find_nearest(icon_palette[i], normal_pal)

        # Apply the per-color shift
        shifted = apply_per_color_shift(
            icon_palette[i], normal_pal[best_idx], shiny_pal[best_idx]
        )
        result.append(shifted)
    return result


def get_used_indices(icon_path):
    """Read icon.png, return set of used palette indices."""
    img = Image.open(icon_path)
    if img.mode != 'P':
        return set()
    return set(img.tobytes())


def parse_species_ids(path):
    species = {}
    with open(path) as f:
        for line in f:
            m = re.match(r'#define\s+(SPECIES_\w+)\s+(\d+)\s*$', line)
            if m:
                species[m.group(1)] = int(m.group(2))
    return species


def parse_icon_paths(path):
    icons = {}
    with open(path) as f:
        for line in f:
            m = re.match(r'const u8 (gMonIcon_\w+)\[\].*INCBIN_U8\("(graphics/pokemon/\w+)/icon\.4bpp"\)', line)
            if m:
                icons[m.group(1)] = m.group(2)
    return icons


def parse_icon_table(path):
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

    species_dirs = {}
    for sp_name, symbol in icon_table.items():
        if symbol in icon_paths:
            species_dirs[sp_name] = icon_paths[symbol]

    std_palettes = {}
    mod_palettes = {}
    stats = {'processed': 0, 'modern': 0}

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

        if not all(os.path.exists(p) for p in [icon_path, normal_pal_path, shiny_pal_path]):
            continue

        pal_idx = pal_indices[sp_name]
        if pal_idx >= len(icon_pals):
            continue

        icon_palette = icon_pals[pal_idx]
        normal_pal = read_jasc_pal(normal_pal_path)
        shiny_pal = read_jasc_pal(shiny_pal_path)
        used_indices = get_used_indices(icon_path)

        result = compute_shiny_icon_palette(icon_palette, normal_pal, shiny_pal, used_indices)
        std_palettes[sp_name] = [rgb_to_gba(*c) for c in result]
        stats['processed'] += 1

        # Modern shiny variant
        if sp_name in MODERN_SHINY_SPECIES:
            mod_shiny_path = os.path.join(gfx_dir, 'shiny_modern.pal')
            if os.path.exists(mod_shiny_path):
                mod_shiny_pal = read_jasc_pal(mod_shiny_path)
                mod_result = compute_shiny_icon_palette(
                    icon_palette, normal_pal, mod_shiny_pal, used_indices
                )
                mod_palettes[sp_name] = [rgb_to_gba(*c) for c in mod_result]
                stats['modern'] += 1

    print(f"Processed {stats['processed']} species, {stats['modern']} modern variants")

    # Show samples
    for sample in ['SPECIES_GENGAR', 'SPECIES_SANDSLASH', 'SPECIES_POLIWHIRL', 'SPECIES_PIKACHU']:
        if sample not in species_dirs or sample not in pal_indices:
            continue
        gfx_dir = os.path.join(PROJECT_ROOT, species_dirs[sample])
        icon_path = os.path.join(gfx_dir, 'icon.png')
        normal_pal = read_jasc_pal(os.path.join(gfx_dir, 'normal.pal'))
        shiny_pal = read_jasc_pal(os.path.join(gfx_dir, 'shiny.pal'))
        pal_idx = pal_indices[sample]
        icon_pal = icon_pals[pal_idx]
        used = get_used_indices(icon_path)

        print(f"\n  {sample} (pal {pal_idx}):")
        for i in sorted(used):
            if i == 0:
                continue
            best_idx, best_dist = find_nearest(icon_pal[i], normal_pal)
            shifted = apply_per_color_shift(icon_pal[i], normal_pal[best_idx], shiny_pal[best_idx])
            print(f"    [{i:2d}] icon{icon_pal[i]} -> normal[{best_idx}]{normal_pal[best_idx]} -> shiny[{best_idx}]{shiny_pal[best_idx]} => {shifted}")

        if sample in mod_palettes:
            mod_shiny_pal = read_jasc_pal(os.path.join(gfx_dir, 'shiny_modern.pal'))
            print(f"    Modern:")
            for i in sorted(used):
                if i == 0:
                    continue
                best_idx, _ = find_nearest(icon_pal[i], normal_pal)
                shifted = apply_per_color_shift(icon_pal[i], normal_pal[best_idx], mod_shiny_pal[best_idx])
                print(f"    [{i:2d}] icon{icon_pal[i]} -> normal[{best_idx}] -> modern[{best_idx}]{mod_shiny_pal[best_idx]} => {shifted}")

    # Write C header
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        f.write("// Auto-generated by tools/pokemon_icon_pal_mapping.py\n")
        f.write("// Precomputed shiny icon palettes using per-color HSV shifts.\n")
        f.write("// Regenerate with: python3 tools/pokemon_icon_pal_mapping.py\n\n")

        f.write("static const u16 sShinyIconPalettes[NUM_SPECIES + 1][16] =\n{\n")
        for sp_name, sp_id in sorted(species_ids.items(), key=lambda x: x[1]):
            if sp_name in std_palettes:
                vals = ', '.join(f'0x{v:04X}' for v in std_palettes[sp_name])
                f.write(f"    [{sp_name}] = {{ {vals} }},\n")
        f.write("};\n\n")

        f.write("static const u16 sShinyModernIconPalettes[NUM_SPECIES + 1][16] =\n{\n")
        for sp_name, sp_id in sorted(species_ids.items(), key=lambda x: x[1]):
            if sp_name in mod_palettes:
                vals = ', '.join(f'0x{v:04X}' for v in mod_palettes[sp_name])
                f.write(f"    [{sp_name}] = {{ {vals} }},\n")
        f.write("};\n")

    print(f"\nWrote {output_path}")


if __name__ == '__main__':
    main()

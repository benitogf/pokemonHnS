#!/usr/bin/env python3
"""Generate precomputed shiny icon palettes via direct color substitution.

For each Pokemon species, for each icon palette color used by that
species' icon, finds the nearest match in the species' normal battle
sprite palette and substitutes the corresponding shiny palette color.

This produces shiny icons that use the actual shiny palette colors,
giving results like cyan Poliwhirl and white modern Gengar.

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

# Species with modern shiny variants (must match C code)
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
    colors = []
    with open(path) as f:
        lines = f.readlines()
    for line in lines[3:]:
        parts = line.strip().split()
        if len(parts) == 3:
            colors.append((int(parts[0]), int(parts[1]), int(parts[2])))
    return colors[:16]


def rgb_to_gba(r, g, b):
    return ((r >> 3) & 0x1F) | (((g >> 3) & 0x1F) << 5) | (((b >> 3) & 0x1F) << 10)


def color_distance(c1, c2):
    """Redmean perceptual color distance."""
    r1, g1, b1 = c1
    r2, g2, b2 = c2
    rmean = (r1 + r2) // 2
    dr, dg, db = r1 - r2, g1 - g2, b1 - b2
    return (2 + rmean // 256) * dr * dr + 4 * dg * dg + (2 + (255 - rmean) // 256) * db * db


def find_nearest(color, palette):
    best_idx, best_dist = 0, 999999
    for j in range(len(palette)):
        d = color_distance(color, palette[j])
        if d < best_dist:
            best_dist = d
            best_idx = j
    return best_idx, best_dist


def build_shiny_palette(icon_palette, normal_pal, shiny_pal, used_indices):
    """Build a shiny icon palette by direct substitution from shiny.pal."""
    result = list(icon_palette)  # Start with original
    for i in range(16):
        if i == 0 or i not in used_indices:
            continue
        best_idx, _ = find_nearest(icon_palette[i], normal_pal)
        result[i] = shiny_pal[best_idx]
    return result


def get_used_indices(icon_path):
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

    icon_pals = [read_jasc_pal(os.path.join(icon_pal_dir, f'icon_palette_{i}.pal')) for i in range(3)]

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

        result = build_shiny_palette(icon_palette, normal_pal, shiny_pal, used_indices)
        std_palettes[sp_name] = [rgb_to_gba(*c) for c in result]
        stats['processed'] += 1

        if sp_name in MODERN_SHINY_SPECIES:
            mod_path = os.path.join(gfx_dir, 'shiny_modern.pal')
            if os.path.exists(mod_path):
                mod_pal = read_jasc_pal(mod_path)
                mod_result = build_shiny_palette(icon_palette, normal_pal, mod_pal, used_indices)
                mod_palettes[sp_name] = [rgb_to_gba(*c) for c in mod_result]
                stats['modern'] += 1

    print(f"Processed {stats['processed']} species, {stats['modern']} modern variants")

    # Show samples
    for sample in ['SPECIES_GENGAR', 'SPECIES_SANDSLASH', 'SPECIES_POLIWHIRL']:
        if sample not in species_dirs or sample not in pal_indices:
            continue
        gfx_dir = os.path.join(PROJECT_ROOT, species_dirs[sample])
        normal_pal = read_jasc_pal(os.path.join(gfx_dir, 'normal.pal'))
        shiny_pal = read_jasc_pal(os.path.join(gfx_dir, 'shiny.pal'))
        pal_idx = pal_indices[sample]
        icon_pal = icon_pals[pal_idx]
        used = get_used_indices(os.path.join(gfx_dir, 'icon.png'))

        print(f"\n  {sample} (pal {pal_idx}):")
        for i in sorted(used):
            if i == 0:
                continue
            best_idx, best_dist = find_nearest(icon_pal[i], normal_pal)
            print(f"    [{i:2d}] {icon_pal[i]} -> shiny[{best_idx}]{shiny_pal[best_idx]}")

        if sample in mod_palettes:
            mod_pal = read_jasc_pal(os.path.join(gfx_dir, 'shiny_modern.pal'))
            print(f"    Modern:")
            for i in sorted(used):
                if i == 0:
                    continue
                best_idx, _ = find_nearest(icon_pal[i], normal_pal)
                print(f"    [{i:2d}] {icon_pal[i]} -> modern[{best_idx}]{mod_pal[best_idx]}")

    # Write C header
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        f.write("// Auto-generated by tools/pokemon_icon_pal_mapping.py\n")
        f.write("// Precomputed shiny icon palettes via direct color substitution.\n")
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

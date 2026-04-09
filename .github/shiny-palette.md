---
name: shiny-palette
description: "GBA Pokémon shiny palette creation — analyze battle/follower sprites, map visual roles to palette indices, generate JASC .pal files, preview across all sprite views. Use when creating or modifying shiny palettes for pokemonHnS."
user-invocable: true
---

# Shiny Palette Creation Guide

## Why This Is Hard

GBA Pokémon sprites use 4bpp indexed palettes (16 colors max). Battle sprites (64×64) and follower sprites (32×32) share the **same base palette**, but the follower artist often uses **different palette indices** to represent visually similar areas because the low resolution merges body/spine/detail regions.

This means `shiny.pal` and `follower_shiny.pal` must assign **different colors to different indices** to achieve the same visual identity. There is no generic formula — each species requires manual analysis.

## File Locations

```
graphics/pokemon/<species>/
  front.png          # Battle front sprite (indexed PNG)
  back.png           # Battle back sprite (indexed PNG)
  normal.pal         # Normal palette (JASC format, CRLF line endings)
  shiny.pal          # Shiny palette (JASC format, CRLF line endings)
  follower_shiny.pal # Follower shiny palette (may not exist yet)

graphics/object_events/pics/pokemon/
  <species>.png      # Follower sprite sheet (multiple 32×32 frames)
```

## Step 1: Analyze Index Usage

For each species, determine which palette indices are actually used by each sprite and how many pixels use each index. This reveals the visual weight of each index.

```python
from PIL import Image
import numpy as np

def analyze_sprite(img_path, crop=None):
    """Return dict of {index: pixel_count} for non-transparent indices."""
    img = Image.open(img_path).convert('P')
    arr = np.array(img)
    if crop:
        x, y, w, h = crop
        arr = arr[y:y+h, x:x+w]
    usage = {}
    for idx in sorted(set(arr.flatten())):
        if idx == 0:  # transparent/bg
            continue
        usage[idx] = int(np.sum(arr == idx))
    return usage

# Battle front — uses full image
battle_usage = analyze_sprite('graphics/pokemon/sandslash/front.png')

# Follower — crop to first 32×32 frame (down-facing)
follower_usage = analyze_sprite(
    'graphics/object_events/pics/pokemon/sandslash.png',
    crop=(0, 0, 32, 32)
)
```

## Step 2: Map Visual Roles

Look at the normal palette colors and the sprites visually. Assign each used index a semantic role:

| Role | Description | Example |
|------|-------------|---------|
| body_bright | Lightest body surface | Yellow highlights |
| body_mid | Mid-tone body | Main body color |
| body_dark | Body shadow | Dark body areas |
| body_darkest | Deep body shadow | Darkest body |
| belly_light | Light underbelly | White/cream |
| belly_mid | Mid underbelly | Grey |
| belly_dark | Dark underbelly | Dark grey |
| spine_highlight | Bright spine/quill | Light brown |
| spine_mid | Mid spine | Brown |
| spine_dark | Dark spine | Dark brown |
| spine_darkest | Deepest spine | Near-black brown |
| detail_dark | Small dark details | Dark grey |
| outline | Hard edge outlines | Near-black |
| highlight | Pure highlight dots | White |

**Key**: The same role may correspond to **different indices** in battle vs follower sprites.

### Example: Sandslash

```
Battle roles (uses ALL 15 indices):
  idx  1 → highlight      (255,255,255)  ×4
  idx  2 → body_bright    (246,238,90)   ×131
  idx  3 → body_mid       (222,197,32)   ×119
  idx  4 → body_dark      (189,164,0)    ×62
  idx  5 → body_darkest   (131,98,0)     ×31
  idx  6 → belly_light    (238,238,222)  ×48
  idx  7 → belly_mid      (205,205,189)  ×65
  idx  8 → belly_dark     (139,139,139)  ×47
  idx  9 → detail_dark    (74,74,74)     ×41
  idx 10 → spine_highlight(205,180,74)   ×130
  idx 11 → spine_mid      (156,123,16)   ×241
  idx 12 → spine_dark     (123,90,0)     ×133
  idx 13 → spine_darkest  (90,49,0)      ×151
  idx 14 → outline        (16,16,16)     ×216

Follower roles (uses only 8 indices):
  idx  5 → body_darkest   (131,98,0)     ×37
  idx  6 → belly_light    (238,238,222)  ×16
  idx  8 → belly_dark     (139,139,139)  ×5
  idx  9 → detail_dark    (74,74,74)     ×4
  idx 10 → *** BODY SURFACE *** (205,180,74) ×61  ← MAIN VISIBLE AREA
  idx 11 → spine_mid      (156,123,16)   ×9
  idx 13 → spine_darkest  (90,49,0)      ×29
  idx 14 → outline        (16,16,16)     ×88
```

**Critical observation**: Follower idx 10 is the **dominant visible surface** (61px) and reads as "body" visually despite being spine_highlight in the palette. In battle, idx 10 is only on spines. The follower merges body+spine into one dominant color at low resolution.

## Step 3: Build the Follower Role Map

Create a mapping: `follower_index → battle_index` based on which battle role each follower index should inherit shiny colors from.

```python
# Sandslash: follower idx → battle idx (for shiny color inheritance)
# Derived by reverse-engineering the committed cream+berry shiny that worked.
SANDSLASH_FOLLOWER_MAP = {
    5:  10,  # follower shadow/spine area (37px) → battle spine_highlight
    10: 2,   # follower dominant body surface (61px) → battle body_bright
    11: 3,   # follower body accent (9px) → battle body_mid
    13: 12,  # follower dark spine area (29px) → battle spine_dark
}
```

**Why these swaps?** At 32×32, the follower artist used palette idx 10 (normally a spine color) for the dominant body surface, and idx 5 (normally body_darkest) for spine areas. So the body↔spine index roles are swapped relative to the battle sprites.

## Step 4: Generate Shiny Palettes

Define target colors by visual role, then map to the correct indices per sprite type.

```python
def make_battle_pal(body, belly, spine, detail_dark, outline, highlight=(255,255,255)):
    """Build a 48-entry flat palette for battle sprites (indices 0-15)."""
    p = [0] * 48
    p[0:3]   = [213, 213, 180]  # idx 0: bg
    p[3:6]   = list(highlight)   # idx 1
    p[6:9]   = list(body[0])     # idx 2: body_bright
    p[9:12]  = list(body[1])     # idx 3: body_mid
    p[12:15] = list(body[2])     # idx 4: body_dark
    p[15:18] = list(body[3])     # idx 5: body_darkest
    p[18:21] = list(belly[0])    # idx 6: belly_light
    p[21:24] = list(belly[1])    # idx 7: belly_mid
    p[24:27] = list(belly[2])    # idx 8: belly_dark
    p[27:30] = list(detail_dark) # idx 9
    p[30:33] = list(spine[0])    # idx 10: spine_highlight
    p[33:36] = list(spine[1])    # idx 11: spine_mid
    p[36:39] = list(spine[2])    # idx 12: spine_dark
    p[39:42] = list(spine[3])    # idx 13: spine_darkest
    p[42:45] = list(outline)     # idx 14
    p[45:48] = [255, 0, 255]    # idx 15: unused
    return p


def make_follower_pal(battle_pal, follower_map):
    """Build follower palette by remapping from the battle palette.

    follower_map: dict of {follower_idx: battle_idx}
    Unmapped indices are copied from battle_pal as-is.
    """
    fpal = list(battle_pal)  # start with a copy
    for f_idx, b_idx in follower_map.items():
        fpal[f_idx*3 : f_idx*3+3] = battle_pal[b_idx*3 : b_idx*3+3]
    return fpal
```

### Usage

```python
body  = [(140,140,155), (100,100,118), (65,65,82), (35,35,50)]
belly = [(175,175,188), (135,135,152), (85,85,102)]
spine = [(120,125,140), (88,92,108), (60,62,78), (35,36,52)]

battle_pal = make_battle_pal(body, belly, spine, detail_dark=(25,25,35), outline=(16,16,16))
follower_pal = make_follower_pal(battle_pal, SANDSLASH_FOLLOWER_MAP)
```

## Step 5: Write JASC .pal Files

```python
def write_pal(path, flat_pal):
    """Write a JASC-PAL file with CRLF line endings (required by gbagfx)."""
    lines = ["JASC-PAL", "0100", "16"]
    for i in range(0, len(flat_pal), 3):
        lines.append(f"{flat_pal[i]} {flat_pal[i+1]} {flat_pal[i+2]}")
    with open(path, 'wb') as f:
        f.write(("\r\n".join(lines) + "\r\n").encode('ascii'))
```

## Step 6: Preview

A full preview MUST include all of these sprites side-by-side for both Normal and Shiny rows:
- **front.png** (64×64) — base front sprite
- **back.png** (64×64) — back sprite
- **anim_front.png frame 1** (top 64×64) — what the game actually uses in battle
- **anim_front.png frame 2** (bottom 64×64) — animation variant frame
- **follower sheet** (full sprite sheet, e.g. 192×32) — all follower poses

Apply `normal.pal` for the Normal row and `shiny.pal`/`follower_shiny.pal` for the Shiny row. The follower column must use `follower_shiny.pal` (not `shiny.pal`) since follower sprites may use different index mappings.

### Extract Colors from Reference Images

When designing a shiny palette from a reference photo or artwork, extract dominant colors:

```python
from PIL import Image

def extract_colors(image_path, num_colors=8):
    img = Image.open(image_path).convert("RGB")
    small = img.resize((150, 150), Image.LANCZOS)
    quantized = small.quantize(colors=num_colors, method=Image.Quantize.MEDIANCUT)
    pal = quantized.getpalette()
    colors = [(pal[i*3], pal[i*3+1], pal[i*3+2]) for i in range(num_colors)]
    colors.sort(key=lambda c: c[0]*0.299 + c[1]*0.587 + c[2]*0.114, reverse=True)
    return colors  # sorted brightest to darkest
```

## Step 7: Verify in Game

```bash
make modern -j$(nproc)
mgba-qt pokeemerald-modern.elf &
```

Check both battle scenes and follower overworld to confirm the shiny colors match visually.

## Common Pitfalls

1. **Assuming follower uses same indices as battle** — the follower sprite artist picks whichever palette colors look right at 32×32 resolution, often collapsing body+spine into one index.
2. **Forgetting CRLF** — JASC .pal files MUST use `\r\n` line endings or gbagfx will fail to parse them.
3. **Palette idx 0** — always (213, 213, 180) for GBA background, treated as transparent.
4. **Not verifying the follower map visually** — always generate a full preview (see Step 6) showing front, back, both anim_front frames, AND the full follower sheet side-by-side before committing.
5. **Using all 16 slots** — idx 15 is typically unused/magenta. Pokémon that use all 16 slots for battle may still skip some for the follower.
6. **Previewing front.png instead of anim_front.png** — the game uses `anim_front.png` for battle, NOT `front.png`. Always preview using `anim_front.png` (both frames). If front.png was updated but anim_front.png wasn't rebuilt, the preview will look correct but the game will show stale pixel data with wrong index assignments. After editing front.png, always rebuild anim_front.png (frame 1 = front.png, frame 2 = animation variant).
7. **Saving sprites with Pillow without tRNS** — always pass `transparency=0` when saving indexed PNGs: `img.save(path, transparency=0)`. Missing tRNS causes "Jumped to invalid address" crashes at runtime.

## Sprite Reindex — Fix Palette Index Scrambling

External sprite editors (Aseprite, GIMP, libresprite) **compact and reorder** palette indices when saving 4bpp indexed PNGs. This breaks palette swaps because shiny/modern palettes are defined by index position.

**Symptoms**: Sprite looks correct with its embedded palette but shiny/modern colors map to wrong visual parts.

### Detection

```python
def check_reindex(png_path, pal_path):
    img = Image.open(png_path)
    p = img.getpalette()[:48]
    png_pal = [(p[i*3], p[i*3+1], p[i*3+2]) for i in range(16)]
    normal = parse_pal(pal_path)
    mismatches = [i for i in range(16) if png_pal[i] != normal[i]]
    if mismatches:
        print(f"REINDEX NEEDED - mismatched indices: {mismatches}")
    else:
        print("No reindex needed")
    return mismatches
```

### Fix

1. Build color→canonical_index mapping from `normal.pal`
2. Remap all pixels to canonical indices
3. Save with canonical palette

```python
def reindex_sprite(png_path, pal_path, output_path=None):
    import shutil
    normal = parse_pal(pal_path)
    img = Image.open(png_path)
    p = img.getpalette()[:48]
    png_pal = [(p[i*3], p[i*3+1], p[i*3+2]) for i in range(16)]

    # Map current idx -> canonical idx by matching colors
    remap = {}
    for ci in range(16):
        color = png_pal[ci]
        if color == (0, 0, 0) and ci > 0:
            continue  # unused/zeroed slot
        for ni in range(16):
            if normal[ni] == color:
                remap[ci] = ni
                break

    pixels = list(img.getdata())
    new_pixels = [remap.get(p, p) for p in pixels]

    w, h = img.size
    new_img = Image.new('P', (w, h))
    flat_pal = []
    for r, g, b in normal:
        flat_pal.extend([r, g, b])
    flat_pal.extend([0] * (768 - len(flat_pal)))
    new_img.putpalette(flat_pal)
    new_img.putdata(new_pixels)

    if output_path is None:
        output_path = png_path
    shutil.copy(png_path, '/tmp/' + png_path.split('/')[-1].replace('.png', '_before_reindex.png'))
    new_img.save(output_path)
```

### Key Details

- **Always check after editing sprites externally** — this happens every time
- Background is always index 0 — typically `(213, 213, 180)` or `(213, 213, 213)`
- Unused palette slots contain `(255, 0, 255)` magenta in canonical palette
- Zeroed `(0, 0, 0)` slots in compacted palette are unused — skip them
- Always backup before reindexing, always verify visually after

## Per-Species Notes

### Sandslash
- Battle uses all 15 indices (1-14 + 0 bg)
- Follower uses only 8 non-bg indices: {5, 6, 8, 9, 10, 11, 13, 14}
- Follower idx 10 = dominant body surface (maps to battle body_mid, idx 3)
- All other follower indices map 1:1 to the same battle index/role

### Gengar
- Battle uses indices 1-6, 11-15 (idx 7-10 are unused magenta)
- Follower uses indices 1-10 after editor compaction — always needs reindex
- Eyes are idx 4-6 (mouth/tongue colors)

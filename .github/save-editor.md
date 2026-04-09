---
name: save-editor
description: "Read and edit pokemonHnS .sav files — decrypt pokemon data, generate valid PIDs, modify party/box pokemon, recalculate checksums. Use when the user wants to inspect or modify a save file."
user-invocable: true
---

# Save File Editor — pokemonHnS

## Save File Structure

128KB (131072 bytes) = 32 sectors × 4096 bytes each.

```
Sector layout (per save slot, 14 sectors):
  Sector  0:     SaveBlock2        (player info, trainer ID, options)
  Sectors 1-4:   SaveBlock1        (party, items, flags, maps)
  Sectors 5-13:  PokemonStorage    (PC boxes)

Two alternating save slots:
  Slot 1: sectors 0-13
  Slot 2: sectors 14-27
  Sectors 28-31: Hall of Fame, Trainer Hill, Recorded Battle
```

### Sector Footer (last 12 bytes of each 4096-byte sector)

```
Offset within sector:
  0xFF4: u16 id         — sector ID (0-13 for save data)
  0xFF6: u16 checksum   — validation checksum
  0xFF8: u32 signature  — must be 0x08012025
  0xFFC: u32 counter    — save counter (higher = newer)
```

### Active Slot Detection

Read sectors 0-13 (slot 1) and 14-27 (slot 2). For each slot, find the sector with `signature == 0x08012025`. Compare `counter` values — the slot with the higher counter is the active save.

### Sector Checksum

```python
def sector_checksum(data_bytes):
    """Calculate sector checksum over data portion (first 4084 bytes)."""
    total = 0
    for i in range(0, 4084, 4):
        total += int.from_bytes(data_bytes[i:i+4], 'little')
        total &= 0xFFFFFFFF
    return ((total >> 16) + total) & 0xFFFF
```

## Party Location

Party is in SaveBlock1 at offset `0x238`. SaveBlock1 spans sectors 1-4 (each 4084 bytes of data).

```
SaveBlock1 offset 0x234: u8 playerPartyCount
SaveBlock1 offset 0x238: struct Pokemon playerParty[6]
```

Since 0x238 < 4084, the party starts in sector 1 (the first SaveBlock1 sector). Each `struct Pokemon` is 100 bytes, so all 6 party members (600 bytes) fit in sector 1.

## PC Box Location

PokemonStorage spans sectors 5-13 (9 sectors × 4084 bytes).

**CRITICAL**: Due to ARM alignment, `struct BoxPokemon boxes[]` starts at offset **4** within PokemonStorage (not offset 1 as the source comment says). The compiler pads 3 bytes after `u8 currentBox` to align the first `u32 personality` field.

```
PokemonStorage offset 0: u8 currentBox
PokemonStorage offset 4: struct BoxPokemon boxes[15][30]  (ARM 4-byte aligned)
```

Each box has 30 BoxPokemon × 80 bytes = 2400 bytes. Box N (0-indexed) starts at offset `4 + N * 2400`.

```python
# To find party data in the .sav file:
# 1. Find active slot
# 2. Locate sector with id=1 in that slot
# 3. Party count at data offset 0x234
# 4. Party data at data offset 0x238, 6 × 100 bytes
```

## Pokemon Data Structure

### struct Pokemon (100 bytes, in party)

```
Offset  Size  Field
0x00    80    struct BoxPokemon (encrypted)
0x50    4     u32 status
0x54    1     u8 level
0x55    1     u8 mail
0x56    2     u16 hp
0x58    2     u16 maxHP
0x5A    2     u16 attack
0x5C    2     u16 defense
0x5E    2     u16 speed
0x60    2     u16 spAttack
0x62    2     u16 spDefense
```

### struct BoxPokemon (80 bytes)

```
Offset  Size  Field
0x00    4     u32 personality (PID)
0x04    4     u32 otId (low16=TID, high16=SID)
0x08    10    nickname (GBA encoding, 0xFF terminated)
0x12    1     language
0x13    1     flags (isBadEgg:1, hasSpecies:1, isEgg:1, blockBoxRS:1, unused:3, inPC:1)
0x14    7     otName (GBA encoding, 0xFF terminated)
0x1B    1     markings
0x1C    2     u16 checksum
0x1E    2     u16 unknown (padding)
0x20    48    encrypted substructs (4 × 12 bytes)
```

### Encryption/Decryption

The 48 bytes at offset 0x20 are XOR-encrypted as 12 u32 words:

```python
def decrypt_pokemon(data_80bytes):
    """Decrypt a BoxPokemon's substruct data in-place."""
    pid = int.from_bytes(data_80bytes[0:4], 'little')
    otid = int.from_bytes(data_80bytes[4:8], 'little')
    key = pid ^ otid
    decrypted = bytearray(data_80bytes[0x20:0x50])
    for i in range(0, 48, 4):
        word = int.from_bytes(decrypted[i:i+4], 'little')
        word ^= key
        decrypted[i:i+4] = word.to_bytes(4, 'little')
    return decrypted

def encrypt_pokemon(data_80bytes, decrypted_substructs):
    """Encrypt substructs back and update checksum."""
    pid = int.from_bytes(data_80bytes[0:4], 'little')
    otid = int.from_bytes(data_80bytes[4:8], 'little')
    key = pid ^ otid
    # Calculate checksum on decrypted data
    checksum = 0
    for i in range(0, 48, 2):
        checksum += int.from_bytes(decrypted_substructs[i:i+2], 'little')
    checksum &= 0xFFFF
    # Encrypt
    encrypted = bytearray(decrypted_substructs)
    for i in range(0, 48, 4):
        word = int.from_bytes(encrypted[i:i+4], 'little')
        word ^= key
        encrypted[i:i+4] = word.to_bytes(4, 'little')
    # Write back
    result = bytearray(data_80bytes)
    result[0x1C:0x1E] = checksum.to_bytes(2, 'little')
    result[0x20:0x50] = encrypted
    return bytes(result)
```

### Substruct Ordering

The 4 substructs (12 bytes each) are stored in a personality-dependent order. `personality % 24` indexes this table:

```python
SUBSTRUCT_ORDER = [
    [0,1,2,3], [0,1,3,2], [0,2,1,3], [0,3,1,2], [0,2,3,1], [0,3,2,1],
    [1,0,2,3], [1,0,3,2], [2,0,1,3], [3,0,1,2], [2,0,3,1], [3,0,2,1],
    [1,2,0,3], [1,3,0,2], [2,1,0,3], [3,1,0,2], [2,3,0,1], [3,2,0,1],
    [1,2,3,0], [1,3,2,0], [2,1,3,0], [3,1,2,0], [2,3,1,0], [3,2,1,0],
]

def get_substruct(decrypted_48bytes, pid, substruct_type):
    """Get the 12-byte substruct of the given type (0-3).
    
    IMPORTANT: order[type] = position (direct indexing).
    Do NOT use .index() — that gives the inverse mapping.
    """
    order = SUBSTRUCT_ORDER[pid % 24]
    position = order[substruct_type]  # order maps type -> position
    return decrypted_48bytes[position*12 : position*12+12]

def set_substruct(decrypted_48bytes, pid, substruct_type, new_12bytes):
    """Set a substruct's 12 bytes."""
    order = SUBSTRUCT_ORDER[pid % 24]
    position = order[substruct_type]  # order maps type -> position
    result = bytearray(decrypted_48bytes)
    result[position*12 : position*12+12] = new_12bytes
    return bytes(result)
```

### Substruct 0 — Growth (12 bytes)

```
Offset  Size  Field
0x00    2     u16 species
0x02    2     u16 heldItem
0x04    4     u32 experience
0x08    1     u8 ppBonuses
0x09    1     u8 friendship
0x0A    1     hiddenNature:5, box_ailment:3
0x0B    1     u8 box_hp
```

### Substruct 1 — Attacks (12 bytes)

```
Offset  Size  Field
0x00    2     u16 move1
0x02    2     u16 move2
0x04    2     u16 move3
0x06    2     u16 move4
0x08    1     u8 pp1
0x09    1     u8 pp2
0x0A    1     u8 pp3
0x0B    1     u8 pp4
```

### Substruct 2 — EVs & Contest (12 bytes)

```
Offset  Size  Field
0x00    1     u8 hpEV
0x01    1     u8 attackEV
0x02    1     u8 defenseEV
0x03    1     u8 speedEV
0x04    1     u8 spAttackEV
0x05    1     u8 spDefenseEV
0x06    1     u8 cool
0x07    1     u8 beauty
0x08    1     u8 cute
0x09    1     u8 smart
0x0A    1     u8 tough
0x0B    1     u8 sheen
```

### Substruct 3 — Misc (12 bytes, bitfields)

```
Offset  Bits   Field
0x00    8      u8 pokerus
0x01    8      u8 metLocation
0x02    0-6    metLevel (7 bits)
0x02    7-10   metGame (4 bits)
0x03    3-7    pokeball (5 bits)
0x03    8      otGender (1 bit) — but positioned as bit within u16
0x04    0-4    hpIV (5 bits)
0x04    5-9    attackIV (5 bits)
0x04    10-14  defenseIV (5 bits)
0x05    15-19  speedIV (5 bits)
0x05    20-24  spAttackIV (5 bits)
0x06    25-29  spDefenseIV (5 bits)
0x07    30     isEgg (1 bit)
0x07    31     abilityNum (1 bit)
0x08-0x0B      ribbons + modernFatefulEncounter (bitfield)
```

```python
def parse_ivs(substruct3_12bytes):
    """Parse IVs from substruct 3."""
    iv_word = int.from_bytes(substruct3_12bytes[4:8], 'little')
    return {
        'hp':    (iv_word >>  0) & 0x1F,
        'atk':   (iv_word >>  5) & 0x1F,
        'def':   (iv_word >> 10) & 0x1F,
        'spd':   (iv_word >> 15) & 0x1F,
        'spa':   (iv_word >> 20) & 0x1F,
        'spd_def': (iv_word >> 25) & 0x1F,
        'is_egg':    (iv_word >> 30) & 1,
        'ability_num': (iv_word >> 31) & 1,
    }

def build_iv_word(hp, atk, dfn, spd, spa, spdef, is_egg=0, ability_num=0):
    """Build the 32-bit IV word for substruct 3."""
    return (
        (hp & 0x1F)
        | ((atk & 0x1F) << 5)
        | ((dfn & 0x1F) << 10)
        | ((spd & 0x1F) << 15)
        | ((spa & 0x1F) << 20)
        | ((spdef & 0x1F) << 25)
        | ((is_egg & 1) << 30)
        | ((ability_num & 1) << 31)
    )
```

## GBA Text Encoding

GBA uses a custom character set, NOT ASCII. String terminator is `0xFF`.

```python
GBA_CHARS = {
    ' ': 0x00,
    '0': 0xA1, '1': 0xA2, '2': 0xA3, '3': 0xA4, '4': 0xA5,
    '5': 0xA6, '6': 0xA7, '7': 0xA8, '8': 0xA9, '9': 0xAA,
    'A': 0xBB, 'B': 0xBC, 'C': 0xBD, 'D': 0xBE, 'E': 0xBF,
    'F': 0xC0, 'G': 0xC1, 'H': 0xC2, 'I': 0xC3, 'J': 0xC4,
    'K': 0xC5, 'L': 0xC6, 'M': 0xC7, 'N': 0xC8, 'O': 0xC9,
    'P': 0xCA, 'Q': 0xCB, 'R': 0xCC, 'S': 0xCD, 'T': 0xCE,
    'U': 0xCF, 'V': 0xD0, 'W': 0xD1, 'X': 0xD2, 'Y': 0xD3,
    'Z': 0xD4, 'a': 0xD5, 'b': 0xD6, 'c': 0xD7, 'd': 0xD8,
    'e': 0xD9, 'f': 0xDA, 'g': 0xDB, 'h': 0xDC, 'i': 0xDD,
    'j': 0xDE, 'k': 0xDF, 'l': 0xE0, 'm': 0xE1, 'n': 0xE2,
    'o': 0xE3, 'p': 0xE4, 'q': 0xE5, 'r': 0xE6, 's': 0xE7,
    't': 0xE8, 'u': 0xE9, 'v': 0xEA, 'w': 0xEB, 'x': 0xEC,
    'y': 0xED, 'z': 0xEE,
}
GBA_DECODE = {v: k for k, v in GBA_CHARS.items()}

def encode_gba_string(text, length):
    """Encode ASCII string to GBA bytes, padded with 0xFF."""
    result = bytearray(length)
    for i in range(length):
        if i < len(text):
            result[i] = GBA_CHARS.get(text[i], 0xAC)  # '?' fallback
        else:
            result[i] = 0xFF
    return bytes(result)

def decode_gba_string(data):
    """Decode GBA bytes to ASCII string, stopping at 0xFF."""
    result = []
    for b in data:
        if b == 0xFF:
            break
        result.append(GBA_DECODE.get(b, '?'))
    return ''.join(result)
```

## PID Generation

PID (personality value) determines: nature, ability slot, gender, shininess, substruct order, and Unown forme. To create valid pokemon, generate a PID that satisfies all desired constraints.

### Shininess Formula

```python
SHINY_ODDS = 8  # pokemonHnS default; configurable via tx_Features_ShinyChance

def is_shiny(pid, otid):
    tid = otid & 0xFFFF
    sid = (otid >> 16) & 0xFFFF
    shiny_value = tid ^ sid ^ (pid >> 16) ^ (pid & 0xFFFF)
    return shiny_value < SHINY_ODDS
```

### Nature

```python
NATURES = [
    'Hardy','Lonely','Brave','Adamant','Naughty',
    'Bold','Docile','Relaxed','Impish','Lax',
    'Timid','Hasty','Serious','Jolly','Naive',
    'Modest','Mild','Quiet','Bashful','Rash',
    'Calm','Gentle','Sassy','Careful','Quirky',
]

def get_nature(pid):
    return NATURES[pid % 25]
```

### Gender

Gender depends on species gender ratio and `(pid & 0xFF)` compared to species threshold. Use `gSpeciesInfo[species].genderRatio` from the codebase.

### Ability

`pid % 2` selects ability slot 0 or 1 (if species has two abilities).

### Generate PID with Constraints

```python
import random

def generate_pid(otid, nature=None, shiny=False, ability=None):
    """Generate a valid PID matching all constraints.

    Args:
        otid: u32 trainer ID (low16=TID, high16=SID)
        nature: int 0-24 or None (any)
        shiny: bool — whether the pokemon should be shiny
        ability: int 0 or 1 or None (any)

    Returns:
        u32 PID satisfying all constraints
    """
    tid = otid & 0xFFFF
    sid = (otid >> 16) & 0xFFFF

    if shiny:
        # For shiny: tid ^ sid ^ pid_hi ^ pid_lo < SHINY_ODDS
        # Pick pid_lo randomly, then compute pid_hi to force shininess
        while True:
            pid_lo = random.randint(0, 0xFFFF)
            # shiny_value = tid ^ sid ^ pid_hi ^ pid_lo must be < SHINY_ODDS
            # So pid_hi = tid ^ sid ^ pid_lo ^ desired_shiny_value
            for sv in range(SHINY_ODDS):
                pid_hi = tid ^ sid ^ pid_lo ^ sv
                pid = (pid_hi << 16) | pid_lo
                if nature is not None and pid % 25 != nature:
                    continue
                if ability is not None and pid % 2 != ability:
                    continue
                return pid
    else:
        # Non-shiny: just match nature and ability
        while True:
            pid = random.randint(0, 0xFFFFFFFF)
            if nature is not None and pid % 25 != nature:
                continue
            if ability is not None and pid % 2 != ability:
                continue
            if is_shiny(pid, otid):
                continue  # accidentally shiny, retry
            return pid
```

### Experience Tables

Level is derived from experience. When setting level, compute the correct exp value from the species' growth rate table. The growth rate is in `gSpeciesInfo[species].growthRate`. Look up `gExperienceTables[growthRate][level]` in `src/data/pokemon/experience_tables.h`.

## Full Read Flow

```python
def read_party(sav_path):
    """Read all party pokemon from a .sav file."""
    with open(sav_path, 'rb') as f:
        sav = bytearray(f.read())

    # 1. Find active save slot
    slot = find_active_slot(sav)

    # 2. Reassemble SaveBlock1 from sectors 1-4 of the active slot
    sb1 = reassemble_block(sav, slot, sector_ids=[1,2,3,4])

    # 3. Read party count and pokemon
    party_count = sb1[0x234]
    party = []
    for i in range(party_count):
        offset = 0x238 + i * 100
        mon_data = sb1[offset:offset+100]
        party.append(parse_pokemon(mon_data))

    return party
```

## Full Write Flow

```python
def write_party(sav_path, output_path, party_pokemon):
    """Write modified party pokemon back to .sav file.

    CRITICAL STEPS:
    1. Encrypt each pokemon's substructs (XOR with pid ^ otid)
    2. Recalculate each pokemon's checksum
    3. Write back to the correct sector(s)
    4. Recalculate each modified sector's checksum
    5. Update sector footer
    """
    # ... implementation follows the reverse of read flow
```

## Common Operations

### Make a Pokemon Shiny

1. Read the pokemon's current PID, OT ID, and nature
2. Generate a new PID that is shiny AND preserves the nature
3. Re-encrypt substructs with new PID (substruct order changes!)
4. Recalculate checksum
5. Write back

**WARNING**: Changing PID changes substruct order. You must:
- Decrypt with OLD pid
- Extract all 4 substructs by type (not position)
- Reorder substructs for NEW pid
- Re-encrypt with NEW pid

```python
def change_pid(box_data_80, new_pid):
    """Change a pokemon's PID, re-ordering and re-encrypting substructs."""
    old_pid = int.from_bytes(box_data_80[0:4], 'little')
    otid = int.from_bytes(box_data_80[4:8], 'little')

    # Decrypt with old key
    decrypted = decrypt_pokemon(box_data_80)

    # Extract substructs by type using OLD order
    # order[type] = position (direct indexing, NOT .index())
    old_order = SUBSTRUCT_ORDER[old_pid % 24]
    substructs_by_type = {}
    for stype in range(4):
        pos = old_order[stype]
        substructs_by_type[stype] = decrypted[pos*12 : pos*12+12]

    # Reorder for NEW pid
    new_order = SUBSTRUCT_ORDER[new_pid % 24]
    new_decrypted = bytearray(48)
    for stype in range(4):
        pos = new_order[stype]
        new_decrypted[pos*12 : pos*12+12] = substructs_by_type[stype]

    # Write new PID and re-encrypt
    result = bytearray(box_data_80)
    result[0:4] = new_pid.to_bytes(4, 'little')
    result = encrypt_pokemon(bytes(result), bytes(new_decrypted))
    return result
```

### Set IVs to Perfect

Modify substruct 3's IV word to all 31s, preserving isEgg and abilityNum bits.

### Change Species/Moves/EVs

Modify the appropriate substruct, recalculate checksum, re-encrypt.

## HnS-Specific Save Fields

SaveBlock1 has custom fields starting at offset `0x3D88`:

```
tx_Random_Chaos, tx_Random_WildPokemon, tx_Random_Types,
tx_Random_Moves, tx_Random_Trainers, tx_Random_Evolutions, etc.
tx_Challenges_Nuzlocke, tx_Challenges_EvoLimit, tx_Challenges_PartyLimit, etc.
tx_Mode_InfiniteTMs, tx_Mode_Mints, tx_Mode_FairyTypes, etc.
tx_Features_ShinyColors, tx_Features_RTCType, tx_Features_ShinyChance, etc.
```

These are individual bytes/flags. The exact offsets depend on build — use `offsetof()` from the source or compare with a known save.

#!/usr/bin/env python3
"""Edit pokemonHnS .sav: make Scyther in Box 1 shiny + hold Master Ball."""

import struct
import random
import sys
import shutil

# --- Constants ---
SECTOR_SIZE = 4096
SECTOR_DATA_SIZE = 4084
SECTOR_SIGNATURE = 0x08012025
NUM_SECTORS_PER_SLOT = 14
BOX_MON_SIZE = 80
IN_BOX_COUNT = 30
SPECIES_SCYTHER = 123
ITEM_MASTER_BALL = 1
SHINY_ODDS = 8

NATURES = [
    'Hardy','Lonely','Brave','Adamant','Naughty',
    'Bold','Docile','Relaxed','Impish','Lax',
    'Timid','Hasty','Serious','Jolly','Naive',
    'Modest','Mild','Quiet','Bashful','Rash',
    'Calm','Gentle','Sassy','Careful','Quirky',
]

SUBSTRUCT_ORDER = [
    [0,1,2,3],[0,1,3,2],[0,2,1,3],[0,3,1,2],[0,2,3,1],[0,3,2,1],
    [1,0,2,3],[1,0,3,2],[2,0,1,3],[3,0,1,2],[2,0,3,1],[3,0,2,1],
    [1,2,0,3],[1,3,0,2],[2,1,0,3],[3,1,0,2],[2,3,0,1],[3,2,0,1],
    [1,2,3,0],[1,3,2,0],[2,1,3,0],[3,1,2,0],[2,3,1,0],[3,2,1,0],
]

GBA_DECODE = {
    0x00: ' ',
    0xA1: '0', 0xA2: '1', 0xA3: '2', 0xA4: '3', 0xA5: '4',
    0xA6: '5', 0xA7: '6', 0xA8: '7', 0xA9: '8', 0xAA: '9',
    0xBB: 'A', 0xBC: 'B', 0xBD: 'C', 0xBE: 'D', 0xBF: 'E',
    0xC0: 'F', 0xC1: 'G', 0xC2: 'H', 0xC3: 'I', 0xC4: 'J',
    0xC5: 'K', 0xC6: 'L', 0xC7: 'M', 0xC8: 'N', 0xC9: 'O',
    0xCA: 'P', 0xCB: 'Q', 0xCC: 'R', 0xCD: 'S', 0xCE: 'T',
    0xCF: 'U', 0xD0: 'V', 0xD1: 'W', 0xD2: 'X', 0xD3: 'Y',
    0xD4: 'Z', 0xD5: 'a', 0xD6: 'b', 0xD7: 'c', 0xD8: 'd',
    0xD9: 'e', 0xDA: 'f', 0xDB: 'g', 0xDC: 'h', 0xDD: 'i',
    0xDE: 'j', 0xDF: 'k', 0xE0: 'l', 0xE1: 'm', 0xE2: 'n',
    0xE3: 'o', 0xE4: 'p', 0xE5: 'q', 0xE6: 'r', 0xE7: 's',
    0xE8: 't', 0xE9: 'u', 0xEA: 'v', 0xEB: 'w', 0xEC: 'x',
    0xED: 'y', 0xEE: 'z',
}

# --- Helpers ---

def decode_gba_string(data):
    result = []
    for b in data:
        if b == 0xFF:
            break
        result.append(GBA_DECODE.get(b, '?'))
    return ''.join(result)

def sector_checksum(data):
    """Checksum over first 4084 bytes of sector data."""
    total = 0
    for i in range(0, SECTOR_DATA_SIZE, 4):
        total += struct.unpack_from('<I', data, i)[0]
        total &= 0xFFFFFFFF
    return ((total >> 16) + total) & 0xFFFF

def read_sector_footer(sector_bytes):
    """Read id, checksum, signature, counter from sector footer."""
    sid = struct.unpack_from('<H', sector_bytes, 0xFF4)[0]
    chk = struct.unpack_from('<H', sector_bytes, 0xFF6)[0]
    sig = struct.unpack_from('<I', sector_bytes, 0xFF8)[0]
    cnt = struct.unpack_from('<I', sector_bytes, 0xFFC)[0]
    return sid, chk, sig, cnt

def find_active_slot(sav):
    """Find which save slot is active (higher counter)."""
    # Slot 1: physical sectors 0-13, Slot 2: physical sectors 14-27
    # Sectors within a slot can be in any physical position
    max_counter = [-1, -1]
    for slot in range(2):
        for phys in range(NUM_SECTORS_PER_SLOT):
            offset = (slot * NUM_SECTORS_PER_SLOT + phys) * SECTOR_SIZE
            sector = sav[offset : offset + SECTOR_SIZE]
            sid, chk, sig, cnt = read_sector_footer(sector)
            if sig == SECTOR_SIGNATURE and cnt > max_counter[slot]:
                max_counter[slot] = cnt
    active = 0 if max_counter[0] >= max_counter[1] else 1
    print(f"Slot 1 counter: {max_counter[0]}, Slot 2 counter: {max_counter[1]}")
    print(f"Active slot: {active + 1}")
    return active

def get_sector_by_id(sav, slot, sector_id):
    """Get the physical sector offset for a given logical sector ID in a slot."""
    base = slot * NUM_SECTORS_PER_SLOT
    for phys in range(NUM_SECTORS_PER_SLOT):
        offset = (base + phys) * SECTOR_SIZE
        sector = sav[offset : offset + SECTOR_SIZE]
        sid, chk, sig, cnt = read_sector_footer(sector)
        if sig == SECTOR_SIGNATURE and sid == sector_id:
            return offset
    raise ValueError(f"Sector ID {sector_id} not found in slot {slot}")

def reassemble_block(sav, slot, sector_ids):
    """Reassemble contiguous data from multiple sectors."""
    data = bytearray()
    for sid in sector_ids:
        offset = get_sector_by_id(sav, slot, sid)
        data.extend(sav[offset : offset + SECTOR_DATA_SIZE])
    return data

def decrypt_substructs(box_data):
    """Decrypt the 48-byte substruct region of an 80-byte BoxPokemon."""
    pid = struct.unpack_from('<I', box_data, 0)[0]
    otid = struct.unpack_from('<I', box_data, 4)[0]
    key = pid ^ otid
    encrypted = bytearray(box_data[0x20:0x50])
    for i in range(0, 48, 4):
        word = struct.unpack_from('<I', encrypted, i)[0]
        word ^= key
        struct.pack_into('<I', encrypted, i, word)
    return bytes(encrypted)

def encrypt_substructs(box_data, decrypted):
    """Encrypt substructs and update checksum. Returns new 80-byte BoxPokemon."""
    pid = struct.unpack_from('<I', box_data, 0)[0]
    otid = struct.unpack_from('<I', box_data, 4)[0]
    key = pid ^ otid
    # Checksum = sum of all u16 in decrypted substructs
    checksum = 0
    for i in range(0, 48, 2):
        checksum += struct.unpack_from('<H', decrypted, i)[0]
    checksum &= 0xFFFF
    # Encrypt
    encrypted = bytearray(decrypted)
    for i in range(0, 48, 4):
        word = struct.unpack_from('<I', encrypted, i)[0]
        word ^= key
        struct.pack_into('<I', encrypted, i, word)
    result = bytearray(box_data)
    struct.pack_into('<H', result, 0x1C, checksum)
    result[0x20:0x50] = encrypted
    return bytes(result)

def get_substruct(decrypted_48, pid, stype):
    """Get 12-byte substruct of given type (0-3)."""
    order = SUBSTRUCT_ORDER[pid % 24]
    pos = order[stype]  # order[type] = position
    return decrypted_48[pos*12 : pos*12+12]

def set_substruct(decrypted_48, pid, stype, new_data):
    """Set 12-byte substruct of given type."""
    order = SUBSTRUCT_ORDER[pid % 24]
    pos = order[stype]  # order[type] = position
    result = bytearray(decrypted_48)
    result[pos*12 : pos*12+12] = new_data
    return bytes(result)

def is_shiny(pid, otid):
    tid = otid & 0xFFFF
    sid = (otid >> 16) & 0xFFFF
    sv = tid ^ sid ^ (pid >> 16) ^ (pid & 0xFFFF)
    return sv < SHINY_ODDS

def generate_shiny_pid(otid, nature_id, ability_bit):
    """Generate a PID that is shiny and matches nature + ability."""
    tid = otid & 0xFFFF
    sid = (otid >> 16) & 0xFFFF
    random.seed()
    attempts = 0
    while True:
        pid_lo = random.randint(0, 0xFFFF)
        for sv in range(SHINY_ODDS):
            pid_hi = tid ^ sid ^ pid_lo ^ sv
            pid = (pid_hi << 16) | pid_lo
            if pid % 25 != nature_id:
                continue
            if pid % 2 != ability_bit:
                continue
            if pid == 0:
                continue
            return pid
        attempts += 1
        if attempts > 1000000:
            raise RuntimeError("Could not generate valid shiny PID")

def change_pid(box_data, new_pid):
    """Change PID, reorder substructs, re-encrypt. Returns new 80-byte BoxPokemon."""
    old_pid = struct.unpack_from('<I', box_data, 0)[0]

    # Decrypt with old key
    decrypted = decrypt_substructs(box_data)

    # Extract substructs by type using OLD ordering
    old_order = SUBSTRUCT_ORDER[old_pid % 24]
    by_type = {}
    for stype in range(4):
        pos = old_order[stype]  # order[type] = position
        by_type[stype] = decrypted[pos*12 : pos*12+12]

    # Reorder for NEW pid
    new_order = SUBSTRUCT_ORDER[new_pid % 24]
    new_decrypted = bytearray(48)
    for stype in range(4):
        pos = new_order[stype]  # order[type] = position
        new_decrypted[pos*12 : pos*12+12] = by_type[stype]

    # Set new PID
    result = bytearray(box_data)
    struct.pack_into('<I', result, 0, new_pid)

    # Encrypt with new key
    return encrypt_substructs(bytes(result), bytes(new_decrypted))

def parse_pokemon_summary(box_data):
    """Parse and display a BoxPokemon's key fields."""
    pid = struct.unpack_from('<I', box_data, 0)[0]
    otid = struct.unpack_from('<I', box_data, 4)[0]
    nickname = decode_gba_string(box_data[0x08:0x12])
    otname = decode_gba_string(box_data[0x14:0x1B])
    checksum = struct.unpack_from('<H', box_data, 0x1C)[0]

    decrypted = decrypt_substructs(box_data)
    sub0 = get_substruct(decrypted, pid, 0)  # Growth
    sub1 = get_substruct(decrypted, pid, 1)  # Attacks
    sub3 = get_substruct(decrypted, pid, 3)  # Misc

    species = struct.unpack_from('<H', sub0, 0)[0]
    item = struct.unpack_from('<H', sub0, 2)[0]
    nature_id = pid % 25
    ability_bit = pid % 2

    moves = [struct.unpack_from('<H', sub1, i*2)[0] for i in range(4)]

    iv_word = struct.unpack_from('<I', sub3, 4)[0]
    ivs = {
        'HP':    (iv_word >>  0) & 0x1F,
        'Atk':   (iv_word >>  5) & 0x1F,
        'Def':   (iv_word >> 10) & 0x1F,
        'Spd':   (iv_word >> 15) & 0x1F,
        'SpA':   (iv_word >> 20) & 0x1F,
        'SpD':   (iv_word >> 25) & 0x1F,
    }

    shiny = is_shiny(pid, otid)

    print(f"  Nickname:  {nickname}")
    print(f"  OT:        {otname}")
    print(f"  Species:   {species}")
    print(f"  PID:       0x{pid:08X}")
    print(f"  OT ID:     0x{otid:08X} (TID={otid & 0xFFFF}, SID={otid >> 16})")
    print(f"  Nature:    {NATURES[nature_id]} ({nature_id})")
    print(f"  Ability:   slot {ability_bit}")
    print(f"  Shiny:     {shiny}")
    print(f"  Held Item: {item}")
    print(f"  Moves:     {moves}")
    print(f"  IVs:       {ivs}")
    print(f"  Checksum:  0x{checksum:04X}")

    return species, pid, otid, nature_id, ability_bit, item


# --- Main ---

def main():
    sav_path = '/root/pokemon/pokemonHnS/pokemonHnS.sav'
    out_path = '/root/pokemon/pokemonHnS/pokemonHnS_modified.sav'

    with open(sav_path, 'rb') as f:
        sav = bytearray(f.read())

    # Trim to 128KB if larger (some emulators add RTC/extra data)
    extra = len(sav) - (32 * SECTOR_SIZE)
    if extra > 0:
        print(f"Save file has {extra} extra bytes (RTC/padding), preserving them")
        sav_extra = sav[32 * SECTOR_SIZE:]
        sav = sav[:32 * SECTOR_SIZE]
    else:
        sav_extra = b''

    # 1. Find active slot
    slot = find_active_slot(sav)

    # 2. Reassemble PokemonStorage from sectors 5-13
    storage_data = reassemble_block(sav, slot, range(5, 14))

    # 3. Scan Box 1 for Scyther
    # PokemonStorage: offset 0 = currentBox, 3 bytes padding, boxes at offset 4
    # Box 1 = boxes[0], starts at storage offset 4
    box1_offset = 4  # within storage_data (ARM alignment pads after u8 currentBox)
    scyther_slot = None
    print("\n=== Scanning Box 1 ===")
    for i in range(IN_BOX_COUNT):
        mon_offset = box1_offset + i * BOX_MON_SIZE
        box_data = bytes(storage_data[mon_offset : mon_offset + BOX_MON_SIZE])
        pid = struct.unpack_from('<I', box_data, 0)[0]
        if pid == 0:
            continue
        decrypted = decrypt_substructs(box_data)
        sub0 = get_substruct(decrypted, pid, 0)
        species = struct.unpack_from('<H', sub0, 0)[0]
        nickname = decode_gba_string(box_data[0x08:0x12])
        if species == SPECIES_SCYTHER:
            print(f"\nFound Scyther at Box 1, slot {i + 1}:")
            parse_pokemon_summary(box_data)
            scyther_slot = i
            break
        else:
            print(f"  Slot {i+1}: species={species} ({nickname})")

    if scyther_slot is None:
        print("\nERROR: No Scyther found in Box 1!")
        sys.exit(1)

    # 4. Read original Scyther data
    mon_offset = box1_offset + scyther_slot * BOX_MON_SIZE
    original_box = bytes(storage_data[mon_offset : mon_offset + BOX_MON_SIZE])
    species, old_pid, otid, nature_id, ability_bit, old_item = parse_pokemon_summary(original_box)

    # 5. Generate shiny PID preserving nature + ability
    print(f"\n=== Generating shiny PID ===")
    print(f"  Target: nature={NATURES[nature_id]}({nature_id}), ability_slot={ability_bit}, shiny=True")
    new_pid = generate_shiny_pid(otid, nature_id, ability_bit)
    print(f"  New PID: 0x{new_pid:08X}")
    print(f"  Verify shiny: {is_shiny(new_pid, otid)}")
    print(f"  Verify nature: {NATURES[new_pid % 25]} ({new_pid % 25})")
    print(f"  Verify ability: slot {new_pid % 2}")

    # 6. Change PID (reorders + re-encrypts substructs)
    modified_box = bytearray(change_pid(original_box, new_pid))

    # 7. Set held item to Master Ball in substruct 0
    decrypted = decrypt_substructs(bytes(modified_box))
    sub0 = bytearray(get_substruct(decrypted, new_pid, 0))
    old_held = struct.unpack_from('<H', sub0, 2)[0]
    struct.pack_into('<H', sub0, 2, ITEM_MASTER_BALL)
    decrypted = set_substruct(decrypted, new_pid, 0, bytes(sub0))
    modified_box = bytearray(encrypt_substructs(bytes(modified_box), decrypted))
    print(f"\n  Held item: {old_held} -> {ITEM_MASTER_BALL} (Master Ball)")

    # 8. Verify final state
    print(f"\n=== Final Pokemon State ===")
    parse_pokemon_summary(bytes(modified_box))

    # 9. Write modified data back into storage
    storage_data[mon_offset : mon_offset + BOX_MON_SIZE] = modified_box

    # 10. Write modified storage sectors back into save
    for sector_id in range(5, 14):
        phys_offset = get_sector_by_id(sav, slot, sector_id)
        # Storage data offset for this sector
        storage_offset = (sector_id - 5) * SECTOR_DATA_SIZE
        chunk = storage_data[storage_offset : storage_offset + SECTOR_DATA_SIZE]

        # Write data
        sav[phys_offset : phys_offset + SECTOR_DATA_SIZE] = chunk

        # Recalculate sector checksum
        new_chk = sector_checksum(sav[phys_offset : phys_offset + SECTOR_DATA_SIZE])
        struct.pack_into('<H', sav, phys_offset + 0xFF6, new_chk)
        print(f"  Sector {sector_id}: checksum updated to 0x{new_chk:04X}")

    # 11. Write output
    with open(out_path, 'wb') as f:
        f.write(sav)
        if sav_extra:
            f.write(sav_extra)

    print(f"\nSaved to: {out_path}")
    print("Done!")

if __name__ == '__main__':
    main()

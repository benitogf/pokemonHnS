#!/usr/bin/env python3
"""Batch edit pokemonHnS .sav: apply multiple pokemon modifications."""

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
PARTY_MON_SIZE = 100  # BoxPokemon(80) + status(4) + level(1) + mail(1) + stats(14)
IN_BOX_COUNT = 30
PARTY_SIZE = 6

# Party offset within SaveBlock1
PARTY_COUNT_OFFSET = 0x234
PARTY_OFFSET = 0x238

NATURES = [
    'Hardy','Lonely','Brave','Adamant','Naughty',
    'Bold','Docile','Relaxed','Impish','Lax',
    'Timid','Hasty','Serious','Jolly','Naive',
    'Modest','Mild','Quiet','Bashful','Rash',
    'Calm','Gentle','Sassy','Careful','Quirky',
]
NATURE_MAP = {n.lower(): i for i, n in enumerate(NATURES)}

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

# Species IDs
SPECIES = {
    'sneasel': 215, 'tangela': 114, 'teddiursa': 216, 'misdreavus': 200,
    'tauros': 128, 'girafarig': 203, 'feraligatr': 160, 'ditto': 132,
    'kabuto': 140, 'vulpix': 37, 'wooper': 194, 'jigglypuff': 39,
    'staryu': 120, 'slowbro': 80, 'scyther': 123, 'annihilape': 449,
    'golbat': 42, 'skarmory': 227, 'mareep': 179, 'grimer': 88,
    'pikachu': 25,
}

# Item IDs
ITEMS = {
    'master_ball': 1, 'big_nugget': 82, 'water_stone': 97,
    'metal_coat': 199, 'light_ball': 202, 'tm_earthquake': 314,
}

# Gender ratios: PERCENT_FEMALE(50) = 127
GENDER_RATIOS = {
    'misdreavus': 127,  # 50% female; PID & 0xFF < 127 = female
}

SHINY_ODDS = 8

# --- Helpers ---

def decode_gba_string(data):
    result = []
    for b in data:
        if b == 0xFF:
            break
        result.append(GBA_DECODE.get(b, '?'))
    return ''.join(result)

def sector_checksum(data):
    total = 0
    for i in range(0, SECTOR_DATA_SIZE, 4):
        total += struct.unpack_from('<I', data, i)[0]
        total &= 0xFFFFFFFF
    return ((total >> 16) + total) & 0xFFFF

def read_sector_footer(sector_bytes):
    sid = struct.unpack_from('<H', sector_bytes, 0xFF4)[0]
    chk = struct.unpack_from('<H', sector_bytes, 0xFF6)[0]
    sig = struct.unpack_from('<I', sector_bytes, 0xFF8)[0]
    cnt = struct.unpack_from('<I', sector_bytes, 0xFFC)[0]
    return sid, chk, sig, cnt

def find_active_slot(sav):
    max_counter = [-1, -1]
    for slot in range(2):
        for phys in range(NUM_SECTORS_PER_SLOT):
            offset = (slot * NUM_SECTORS_PER_SLOT + phys) * SECTOR_SIZE
            sector = sav[offset : offset + SECTOR_SIZE]
            sid, chk, sig, cnt = read_sector_footer(sector)
            if sig == SECTOR_SIGNATURE and cnt > max_counter[slot]:
                max_counter[slot] = cnt
    active = 0 if max_counter[0] >= max_counter[1] else 1
    print(f"Slot 1 counter: {max_counter[0]}, Slot 2 counter: {max_counter[1]}, Active: {active + 1}")
    return active

def get_sector_by_id(sav, slot, sector_id):
    base = slot * NUM_SECTORS_PER_SLOT
    for phys in range(NUM_SECTORS_PER_SLOT):
        offset = (base + phys) * SECTOR_SIZE
        sector = sav[offset : offset + SECTOR_SIZE]
        sid, chk, sig, cnt = read_sector_footer(sector)
        if sig == SECTOR_SIGNATURE and sid == sector_id:
            return offset
    raise ValueError(f"Sector ID {sector_id} not found in slot {slot}")

def reassemble_block(sav, slot, sector_ids):
    data = bytearray()
    for sid in sector_ids:
        offset = get_sector_by_id(sav, slot, sid)
        data.extend(sav[offset : offset + SECTOR_DATA_SIZE])
    return data

def decrypt_substructs(box_data):
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
    pid = struct.unpack_from('<I', box_data, 0)[0]
    otid = struct.unpack_from('<I', box_data, 4)[0]
    key = pid ^ otid
    checksum = 0
    for i in range(0, 48, 2):
        checksum += struct.unpack_from('<H', decrypted, i)[0]
    checksum &= 0xFFFF
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
    order = SUBSTRUCT_ORDER[pid % 24]
    pos = order[stype]
    return decrypted_48[pos*12 : pos*12+12]

def set_substruct(decrypted_48, pid, stype, new_data):
    order = SUBSTRUCT_ORDER[pid % 24]
    pos = order[stype]
    result = bytearray(decrypted_48)
    result[pos*12 : pos*12+12] = new_data
    return bytes(result)

def is_shiny(pid, otid):
    tid = otid & 0xFFFF
    sid = (otid >> 16) & 0xFFFF
    sv = tid ^ sid ^ (pid >> 16) ^ (pid & 0xFFFF)
    return sv < SHINY_ODDS

def generate_shiny_pid(otid, nature_id, ability_bit, female=False, gender_ratio=None):
    """Generate a PID that is shiny and matches nature + ability + optional gender."""
    tid = otid & 0xFFFF
    sid = (otid >> 16) & 0xFFFF
    random.seed()
    attempts = 0
    while True:
        pid_lo = random.randint(0, 0xFFFF)
        for sv in range(SHINY_ODDS):
            pid_hi = tid ^ sid ^ pid_lo ^ sv
            pid = (pid_hi << 16) | pid_lo
            if pid == 0:
                continue
            if pid % 25 != nature_id:
                continue
            if pid % 2 != ability_bit:
                continue
            if female and gender_ratio is not None:
                if (pid & 0xFF) >= gender_ratio:
                    continue
            return pid
        attempts += 1
        if attempts > 2000000:
            raise RuntimeError("Could not generate valid shiny PID")

def generate_nature_pid(otid, nature_id, ability_bit, old_pid, female=False, gender_ratio=None):
    """Generate a non-shiny PID that matches nature + ability + optional gender, preserving shininess of original."""
    # Check if already correct nature
    if old_pid % 25 == nature_id:
        if female and gender_ratio is not None:
            if (old_pid & 0xFF) >= gender_ratio:
                pass  # need new PID for gender
            else:
                return old_pid
        else:
            return old_pid
    random.seed()
    shiny = is_shiny(old_pid, otid)
    attempts = 0
    while True:
        pid = random.randint(1, 0xFFFFFFFF)
        if pid % 25 != nature_id:
            continue
        if pid % 2 != ability_bit:
            continue
        if is_shiny(pid, otid) != shiny:
            continue
        if female and gender_ratio is not None:
            if (pid & 0xFF) >= gender_ratio:
                continue
        return pid
        attempts += 1
        if attempts > 2000000:
            raise RuntimeError("Could not generate valid PID")

def change_pid(box_data, new_pid):
    old_pid = struct.unpack_from('<I', box_data, 0)[0]
    decrypted = decrypt_substructs(box_data)
    old_order = SUBSTRUCT_ORDER[old_pid % 24]
    by_type = {}
    for stype in range(4):
        pos = old_order[stype]
        by_type[stype] = decrypted[pos*12 : pos*12+12]
    new_order = SUBSTRUCT_ORDER[new_pid % 24]
    new_decrypted = bytearray(48)
    for stype in range(4):
        pos = new_order[stype]
        new_decrypted[pos*12 : pos*12+12] = by_type[stype]
    result = bytearray(box_data)
    struct.pack_into('<I', result, 0, new_pid)
    return encrypt_substructs(bytes(result), bytes(new_decrypted))

def parse_pokemon(box_data, verbose=True):
    """Parse BoxPokemon and return key fields."""
    pid = struct.unpack_from('<I', box_data, 0)[0]
    otid = struct.unpack_from('<I', box_data, 4)[0]
    nickname = decode_gba_string(box_data[0x08:0x12])
    decrypted = decrypt_substructs(box_data)
    sub0 = get_substruct(decrypted, pid, 0)
    species = struct.unpack_from('<H', sub0, 0)[0]
    item = struct.unpack_from('<H', sub0, 2)[0]
    nature_id = pid % 25
    ability_bit = pid % 2
    shiny = is_shiny(pid, otid)
    if verbose:
        print(f"    Species={species} Name={nickname} Nature={NATURES[nature_id]} "
              f"Shiny={shiny} Item={item} PID=0x{pid:08X} Ability={ability_bit}")
    return species, pid, otid, nature_id, ability_bit, item, shiny


# --- Modifications spec ---
# Each entry: (location, species_name, changes_dict)
# location: ('box', box_num) or ('party',)
# changes: shiny=bool, nature=str, item=str, female=bool

EDITS = [
    # 1. sneasel box 13: adamant, shiny, master ball
    (('box', 13), 'sneasel', {'shiny': True, 'nature': 'adamant', 'item': 'master_ball'}),
    # 2. tangela box 13: modest, shiny, master ball
    (('box', 13), 'tangela', {'shiny': True, 'nature': 'modest', 'item': 'master_ball'}),
    # 3. teddiursa box 2: adamant, shiny, master ball
    (('box', 2), 'teddiursa', {'shiny': True, 'nature': 'adamant', 'item': 'master_ball'}),
    # 4. misdreavus box 2: female, timid, shiny, master ball
    (('box', 2), 'misdreavus', {'shiny': True, 'nature': 'timid', 'item': 'master_ball', 'female': True}),
    # 5. tauros box 2: shiny, master ball
    (('box', 2), 'tauros', {'shiny': True, 'item': 'master_ball'}),
    # 6. girafarig box 2: shiny, master ball
    (('box', 2), 'girafarig', {'shiny': True, 'item': 'master_ball'}),
    # 7. feraligatr box 1: shiny, serious, master ball
    (('box', 1), 'feraligatr', {'shiny': True, 'nature': 'serious', 'item': 'master_ball'}),
    # 8. ditto box 1: shiny, big nugget
    (('box', 1), 'ditto', {'shiny': True, 'item': 'big_nugget'}),
    # 9. kabuto box 1: shiny, lonely, big nugget
    (('box', 1), 'kabuto', {'shiny': True, 'nature': 'lonely', 'item': 'big_nugget'}),
    # 10. vulpix box 1: shiny, modest, big nugget
    (('box', 1), 'vulpix', {'shiny': True, 'nature': 'modest', 'item': 'big_nugget'}),
    # 11. wooper box 1: shiny, big nugget
    (('box', 1), 'wooper', {'shiny': True, 'item': 'big_nugget'}),
    # 12. jigglypuff box 1: shiny, TM earthquake
    (('box', 1), 'jigglypuff', {'shiny': True, 'item': 'tm_earthquake'}),
    # 13. staryu box 2: shiny, modest, big nugget
    (('box', 2), 'staryu', {'shiny': True, 'nature': 'modest', 'item': 'big_nugget'}),
    # 14. slowbro party: calm, big nugget (NOT shiny)
    (('party',), 'slowbro', {'nature': 'calm', 'item': 'big_nugget'}),
    # 15. scyther party: shiny, big nugget
    (('party',), 'scyther', {'shiny': True, 'item': 'big_nugget'}),
    # 16. annihilape party: jolly, big nugget (NOT shiny)
    (('party',), 'annihilape', {'nature': 'jolly', 'item': 'big_nugget'}),
    # 17. golbat party: jolly, shiny, big nugget
    (('party',), 'golbat', {'shiny': True, 'nature': 'jolly', 'item': 'big_nugget'}),
    # 18. skarmory party: impish, shiny, big nugget
    (('party',), 'skarmory', {'shiny': True, 'nature': 'impish', 'item': 'big_nugget'}),
    # 19. mareep box 1: shiny, timid, water stone
    (('box', 1), 'mareep', {'shiny': True, 'nature': 'timid', 'item': 'water_stone'}),
    # 20. grimer box 1: shiny, careful, metal coat
    (('box', 1), 'grimer', {'shiny': True, 'nature': 'careful', 'item': 'metal_coat'}),
    # 21. pikachu box 3: shiny, light ball
    (('box', 3), 'pikachu', {'shiny': True, 'item': 'light_ball'}),
]


def find_pokemon_in_box(storage_data, box_num, species_id):
    """Find a pokemon by species in a box. Returns (slot_index, mon_offset)."""
    # Box N starts at offset 4 + (N-1) * 30 * 80
    box_offset = 4 + (box_num - 1) * IN_BOX_COUNT * BOX_MON_SIZE
    for i in range(IN_BOX_COUNT):
        mon_offset = box_offset + i * BOX_MON_SIZE
        box_data = bytes(storage_data[mon_offset : mon_offset + BOX_MON_SIZE])
        pid = struct.unpack_from('<I', box_data, 0)[0]
        if pid == 0:
            continue
        decrypted = decrypt_substructs(box_data)
        sub0 = get_substruct(decrypted, pid, 0)
        species = struct.unpack_from('<H', sub0, 0)[0]
        if species == species_id:
            return i, mon_offset
    return None, None


def find_pokemon_in_party(party_data, party_count, species_id):
    """Find a pokemon by species in party. Returns (slot_index, mon_offset)."""
    for i in range(party_count):
        mon_offset = i * PARTY_MON_SIZE
        box_data = bytes(party_data[mon_offset : mon_offset + BOX_MON_SIZE])
        pid = struct.unpack_from('<I', box_data, 0)[0]
        if pid == 0:
            continue
        decrypted = decrypt_substructs(box_data)
        sub0 = get_substruct(decrypted, pid, 0)
        species = struct.unpack_from('<H', sub0, 0)[0]
        if species == species_id:
            return i, mon_offset
    return None, None


def apply_edit(box_data_80, changes, species_name):
    """Apply modifications to an 80-byte BoxPokemon. Returns modified bytes."""
    species, pid, otid, nature_id, ability_bit, item, shiny = parse_pokemon(box_data_80, verbose=True)
    modified = bytearray(box_data_80)

    want_shiny = changes.get('shiny', False)
    want_nature = changes.get('nature', None)
    want_item = changes.get('item', None)
    want_female = changes.get('female', False)

    target_nature = NATURE_MAP[want_nature] if want_nature else nature_id
    gender_ratio = GENDER_RATIOS.get(species_name) if want_female else None

    need_new_pid = False
    if want_shiny and not shiny:
        need_new_pid = True
    if want_nature and nature_id != target_nature:
        need_new_pid = True
    if want_female and gender_ratio is not None and (pid & 0xFF) >= gender_ratio:
        need_new_pid = True

    if need_new_pid:
        if want_shiny:
            new_pid = generate_shiny_pid(otid, target_nature, ability_bit,
                                         female=want_female, gender_ratio=gender_ratio)
        else:
            new_pid = generate_nature_pid(otid, target_nature, ability_bit, pid,
                                          female=want_female, gender_ratio=gender_ratio)
        print(f"    -> New PID: 0x{new_pid:08X} Nature={NATURES[new_pid % 25]} "
              f"Shiny={is_shiny(new_pid, otid)} Ability={new_pid % 2}")
        if want_female and gender_ratio:
            print(f"    -> Gender: {'Female' if (new_pid & 0xFF) < gender_ratio else 'Male'}")
        modified = bytearray(change_pid(bytes(modified), new_pid))
        pid = new_pid

    if want_item:
        item_id = ITEMS[want_item]
        decrypted = decrypt_substructs(bytes(modified))
        sub0 = bytearray(get_substruct(decrypted, pid, 0))
        old_item = struct.unpack_from('<H', sub0, 2)[0]
        struct.pack_into('<H', sub0, 2, item_id)
        decrypted = set_substruct(decrypted, pid, 0, bytes(sub0))
        modified = bytearray(encrypt_substructs(bytes(modified), decrypted))
        print(f"    -> Item: {old_item} -> {item_id} ({want_item})")

    return bytes(modified)


def write_sectors_back(sav, slot, sector_ids, block_data):
    """Write reassembled block data back to save sectors and update checksums."""
    for sector_id in sector_ids:
        phys_offset = get_sector_by_id(sav, slot, sector_id)
        storage_offset = (sector_id - sector_ids[0]) * SECTOR_DATA_SIZE
        chunk = block_data[storage_offset : storage_offset + SECTOR_DATA_SIZE]
        sav[phys_offset : phys_offset + SECTOR_DATA_SIZE] = chunk
        new_chk = sector_checksum(sav[phys_offset : phys_offset + SECTOR_DATA_SIZE])
        struct.pack_into('<H', sav, phys_offset + 0xFF6, new_chk)


def main():
    sav_path = '/root/pokemon/pokemonHnS/pokemonHnS.sav'
    out_path = '/root/pokemon/pokemonHnS/pokemonHnS_modified.sav'

    with open(sav_path, 'rb') as f:
        sav = bytearray(f.read())

    extra = len(sav) - (32 * SECTOR_SIZE)
    if extra > 0:
        print(f"Save file has {extra} extra bytes (RTC/padding), preserving them")
        sav_extra = sav[32 * SECTOR_SIZE:]
        sav = sav[:32 * SECTOR_SIZE]
    else:
        sav_extra = b''

    slot = find_active_slot(sav)

    # Reassemble storage (sectors 5-13) and SaveBlock1 (sectors 1-4)
    storage_data = reassemble_block(sav, slot, range(5, 14))
    sb1_data = reassemble_block(sav, slot, range(1, 5))

    # Read party count
    party_count = sb1_data[PARTY_COUNT_OFFSET]
    print(f"Party count: {party_count}")

    # Extract party data
    party_start = PARTY_OFFSET
    party_end = party_start + PARTY_SIZE * PARTY_MON_SIZE

    storage_modified = False
    sb1_modified = False
    success = 0
    errors = []

    for i, (location, species_name, changes) in enumerate(EDITS):
        species_id = SPECIES[species_name]
        label = f"#{i+1} {species_name.capitalize()}"

        if location[0] == 'box':
            box_num = location[1]
            print(f"\n=== {label} (Box {box_num}) ===")
            slot_idx, mon_offset = find_pokemon_in_box(storage_data, box_num, species_id)
            if slot_idx is None:
                errors.append(f"{label}: NOT FOUND in Box {box_num}")
                print(f"  ERROR: {species_name} not found in Box {box_num}!")
                continue
            print(f"  Found at Box {box_num}, slot {slot_idx + 1}")
            box_bytes = bytes(storage_data[mon_offset : mon_offset + BOX_MON_SIZE])
            new_bytes = apply_edit(box_bytes, changes, species_name)
            storage_data[mon_offset : mon_offset + BOX_MON_SIZE] = new_bytes
            storage_modified = True
            success += 1

        elif location[0] == 'party':
            print(f"\n=== {label} (Party) ===")
            slot_idx, mon_offset = find_pokemon_in_party(
                sb1_data[party_start:party_end], party_count, species_id)
            if slot_idx is None:
                errors.append(f"{label}: NOT FOUND in Party")
                print(f"  ERROR: {species_name} not found in Party!")
                continue
            print(f"  Found at Party slot {slot_idx + 1}")
            abs_offset = party_start + mon_offset
            box_bytes = bytes(sb1_data[abs_offset : abs_offset + BOX_MON_SIZE])
            new_bytes = apply_edit(box_bytes, changes, species_name)
            sb1_data[abs_offset : abs_offset + BOX_MON_SIZE] = new_bytes
            sb1_modified = True
            success += 1

    # Write back modified sectors
    if storage_modified:
        print("\n=== Writing storage sectors 5-13 ===")
        write_sectors_back(sav, slot, list(range(5, 14)), storage_data)

    if sb1_modified:
        print("\n=== Writing SaveBlock1 sectors 1-4 ===")
        write_sectors_back(sav, slot, list(range(1, 5)), sb1_data)

    # Write output
    with open(out_path, 'wb') as f:
        f.write(sav)
        if sav_extra:
            f.write(sav_extra)

    print(f"\n{'='*50}")
    print(f"Results: {success}/{len(EDITS)} edits applied")
    if errors:
        print(f"Errors ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
    print(f"Saved to: {out_path}")

if __name__ == '__main__':
    main()

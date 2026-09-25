#!/usr/bin/env python3
"""Check public T4 data and worked-example arithmetic, never an engine or oracle.

These checks explain numbers already printed in SPEC-v1.1.md. They cannot
establish implementation correctness or replace the independent T4 suite.
"""

import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PERKS = ['whetstone', 'quickdraw', 'bulwark', 'fortune']
SLOTS = {'Normal', 'Damage', 'Lifesteal', 'Crit', 'Magic', 'Melee'}
MELEE = {'Sword', 'Axe', 'Dagger', 'Spear', 'Mace'}
MAGIC = {'Spell', 'Artifact'}
FROZEN_T4 = {'t4/SPEC-v1.1.md', 't4/data/items-v1.1.json',
             't4/data/encounters-v1.1.json'}


def check(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    # JSON's default parser silently overwrites duplicate keys.
    def unique(pairs):
        result = {}
        for key, value in pairs:
            check(key not in result, f'{path}: duplicate key {key}')
            result[key] = value
        return result

    return json.loads((ROOT / path).read_text(), object_pairs_hook=unique)


def hashes(manifest, allowed):
    seen = set()
    for line in (ROOT / manifest).read_text().splitlines():
        digest, name = line.split('  ', 1)
        check(name in allowed and name not in seen, f'{manifest}: unexpected {name}')
        seen.add(name)
        check(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest,
              f'{manifest}: hash mismatch for {name}')
    check(seen == allowed, f'{manifest}: missing files')
    return len(seen)


def crit_rolls(seed, count):
    # v1.0 section 1.1 uses 32-bit multiplication, unlike Python integers.
    mask = 0xFFFFFFFF
    state = seed ^ 0x1B873593
    for _ in range(count):
        state = (state + 0x6D2B79F5) & mask
        t = ((state ^ (state >> 15)) * (state | 1)) & mask
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & mask)) & mask
        yield ((t ^ (t >> 14)) & mask) * 100 // 2**32


def validate():
    base = read_json('seed/data/items.json')
    added = read_json('t4/data/items-v1.1.json')
    encounters = read_json('t4/data/encounters-v1.1.json')
    spec = (ROOT / 't4/SPEC-v1.1.md').read_text()
    check(len(base) == 20 and len(added) == 1, 'item counts')
    ids = [item['id'] for item in base + added]
    check(len(ids) == len(set(ids)), 'duplicate item ids')
    items = {item['id']: item for item in base + added}
    needle = added[0]
    check(set(needle) == set(base[0]), 'item schema')
    check((needle['id'], needle['name'], needle['type'], needle['tag']) ==
          ('hummingbird_needle', 'Hummingbird Needle', 'Dagger', 'Neutral'), 'needle identity')
    check(len(needle['tiers']) == 4, 'four needle rarities')
    for rarity, (tier, expected) in enumerate(zip(
            needle['tiers'], [(30, 2, 3), (72, 3, 6), (144, 4, 10), (360, 6, 15)])):
        check(set(tier) == set(base[0]['tiers'][0]), 'tier schema')
        cost, damage, multicast = expected
        check((tier['cost'], tier['cooldown'], tier['damage'], tier['multicast']) ==
              (cost, 1.0, damage, multicast), f'needle tier {rarity}')
        check(tier['startOfCombat'] is None, 'needle startOfCombat')
        for field in set(tier) - {'cost', 'cooldown', 'damage', 'multicast', 'startOfCombat'}:
            check(type(tier[field]) is int and tier[field] == 0, f'needle {field}')
        row = f'| {rarity} ' + ['Common', 'Rare', 'Epic', 'Legendary'][rarity]
        check(f'{row} | {cost} | 1.0 | {damage} | {multicast} |' in spec, 'needle table')

    encounter_ids = [e['id'] for e in encounters]
    check(encounter_ids == ['goblin_ambush', 'needle_swarm', 'iron_warden',
                            'ember_court', 'gilded_hoard'], 'encounter order')
    rewards = []
    for encounter in encounters:
        check(set(encounter) == {'id', 'name', 'hp', 'slots', 'entries', 'reward'},
              'encounter schema')
        check(type(encounter['hp']) is int and encounter['hp'] > 0, 'encounter hp')
        check(len(encounter['slots']) == 10 and set(encounter['slots']) <= SLOTS, 'slots')
        occupied = set()
        for entry in encounter['entries']:
            check(set(entry) == {'slot', 'itemId', 'rarity'}, 'entry schema')
            slot, item_id, rarity = entry['slot'], entry['itemId'], entry['rarity']
            check(type(slot) is int and 0 <= slot < 10 and slot not in occupied, 'slot index')
            occupied.add(slot)
            check(item_id in items and type(rarity) is int and 0 <= rarity <= 3, 'item/rarity')
            item = items[item_id]
            slot_type = encounter['slots'][slot]
            check(slot_type != 'Magic' or item['type'] in MAGIC, 'Magic restriction')
            check(slot_type != 'Melee' or item['type'] in MELEE, 'Melee restriction')
            tier = item['tiers'][rarity]
            check(all(tier[k] == 0 for k in ('shock', 'heal', 'lifesteal'))
                  and slot_type != 'Lifesteal', 'encounter outside specified domain')
        reward = encounter['reward']
        check(set(reward) == {'gold', 'perk', 'item'}, 'reward schema')
        check(type(reward['gold']) is int and reward['gold'] >= 0, 'reward gold')
        check(reward['perk'] is None or reward['perk'] in PERKS, 'reward perk')
        if reward['perk'] is not None:
            rewards.append(reward['perk'])
        if reward['item'] is not None:
            reward_item = reward['item']
            check(set(reward_item) == {'itemId', 'rarity'}, 'reward item schema')
            check(reward_item['itemId'] in items and type(reward_item['rarity']) is int
                  and 0 <= reward_item['rarity'] <= 3, 'reward item')
        tower_text = ', '.join(
            str(e['slot']) + (f" (`{encounter['slots'][e['slot']]}` slot)"
                             if encounter['slots'][e['slot']] != 'Normal' else '')
            + f": {e['itemId']} ({e['rarity']})" for e in encounter['entries'])
        reward_text = (f"{reward['item']['itemId']} ({reward['item']['rarity']})"
                       if reward['item'] else 'none')
        row = (f"| `{encounter['id']}` | {encounter['hp']} | {tower_text} | "
               f"{reward['gold']} | {reward['perk'] or 'none'} | {reward_text} |")
        check(row in spec, 'encounter table disagrees with data')
    check(set(rewards) == set(PERKS), 'all four perks have encounter rewards')

    # No v1.0 primary/multicast windows overlap, even at the cooldown floor.
    for item in base:
        for tier in item['tiers']:
            cadence = math.floor(max(1, tier['cooldown']) * 10 + 0.5)
            check(tier['multicast'] < cadence, 'v1.0 cap reachability claim')

    # Count candidates per frame directly; this is arithmetic for the public table.
    expected_rows = [(237, 237, 0, 177), (240, 240, 0, 180),
                     (414, 414, 0, 354), (419, 419, 0, 359),
                     (650, 591, 59, 531), (655, 596, 59, 536),
                     (940, 591, 349, 531), (950, 596, 354, 536)]
    for rarity, tier in enumerate(needle['tiers']):
        for quick in (False, True):
            primaries = list(range(5 if quick else 10, 601, 10))
            counts = [sum(p <= frame <= p + tier['multicast'] for p in primaries)
                      for frame in range(1, 601)]
            candidates, entries = sum(counts), sum(n > 0 for n in counts)
            actual = (candidates, entries, candidates - entries, entries - len(primaries))
            check(actual == expected_rows[rarity * 2 + quick], 'schedule counts')
            check('| ' + ' | '.join(map(str, actual)) + ' |' in spec, 'schedule table')
    check(60 + 59 * 2 == 178, 'v1.0 dagger schedule example')
    for seconds, first in [(1, 5), (2, 10), (2.5, 13), (3, 15), (3.5, 18),
                           (4, 20), (5, 25), (6, 30), (8, 40)]:
        check(math.ceil(seconds * 10 / 2) == first, 'quickdraw table')

    def tier(item_id, rarity=0):
        return items[item_id]['tiers'][rarity]

    # Each line below checks a stated public example, not an engine's output.
    epic_damage = tier('hummingbird_needle', 2)['damage']
    check(9 + math.ceil(1000 / epic_damage) == 259, '12.2a end frame')
    check(259 // 10 == 25 and 250 - 25 == 225, '12.2a trigger flags')
    crits = [frame for frame, roll in zip(range(10, 601), crit_rolls(12345, 591)) if roll < 15]
    check(len(crits) == 76 and crits[:5] == [11, 24, 25, 43, 50], '12.2b crit draws')
    check(100000 - epic_damage * (591 + len(crits)) == 97332, '12.2b hp')
    double_damage = 2 * tier('hummingbird_needle', 3)['damage']
    hits = math.ceil(1000 / double_damage)
    check((9 + hits, 2 * hits, 1000 - hits * double_damage) == (93, 168, -8), '12.2c')
    sword = tier('iron_sword')['damage']
    check((13 + 4 * 25, 100 - 5 * sword) == (113, -10), '12.3')
    check([sword + 10, math.floor(tier('iron_sword', 1)['damage'] * 1.2),
           tier('ember_orb')['damage'], tier('storm_bow')['damage'] + tier('storm_bow')['stormBonus'],
           tier('tempest_blade')['damage'] + tier('tempest_blade')['stormBonus'],
           tier('glacier_shield')['damage']] == [32, 52, 0, 17, 24, 0], '12.4')
    check(1000 - (20 * tier('bone_club')['damage'] - 100) == 600, '12.5')
    check(40 + 5 * 1 + 10 == 55, '12.6')
    check(42 ^ 0x68E31DA4 ^ 1 == 1759714703, '12.7 seed')
    axe, sword_rare = tier('war_axe', 1)['damage'], tier('iron_sword', 1)['damage']
    goblin, swarm, warden = encounters[:3]
    check((1000 - 3 * 25 - 4 * sword, goblin['hp'] - 2 * axe - 4 * sword_rare)
          == (837, -116), '12.7a')
    check(goblin['hp'] - axe - 3 * sword_rare > 0, '12.7a alive before 100')
    check((1000 - 5 * (axe + 10) - 8 * tier('bone_club', 1)['damage'], warden['hp'] - 25)
          == (-50, 1175), '12.7b')
    check(1000 - 4 * (axe + 10) - 8 * tier('bone_club', 1)['damage'] > 0, '12.7b before 250')
    check((1000 - 141 * epic_damage,
           swarm['hp'] - 3 * axe - 6 * sword_rare + 5 * tier('tower_shield')['shield'])
          == (436, -74), '12.7c')
    check(swarm['hp'] - 2 * axe - 5 * sword_rare + 4 * tier('tower_shield')['shield'] > 0,
          '12.7c alive before 150')
    check(45 + goblin['reward']['gold'] + needle['tiers'][0]['cost'] // 2 == 80, '12.7d refund')
    check(min(set(range(10)) - {0, 3, 5}) == min({1, 3, 5}) == 1, '12.7e merge slot')

    # Only public seed files are eligible; never follow arbitrary manifest paths.
    legacy_paths = {line.split('  ', 1)[1] for line in (ROOT / 'FROZEN.sha256').read_text().splitlines()}
    check(all(Path(p).parts[0] == 'seed' and '..' not in Path(p).parts for p in legacy_paths),
          'legacy manifest must contain only seed paths')
    count = hashes('FROZEN.sha256', legacy_paths)
    hashes('t4/FROZEN.sha256', FROZEN_T4)
    print(f'OK: T4 data, public example arithmetic, {count} legacy hashes and 3 T4 hashes')


if __name__ == '__main__':
    validate()

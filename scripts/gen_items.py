#!/usr/bin/env python3
"""Generates seed/data/items.json. Provenance script for a frozen artifact.
Scaling is fully deterministic: no RNG."""
import json, os

# base = common-tier values; scale rules applied for rare/epic/legendary
COST_MUL = [1.0, 2.4, 4.8, 12.0]
DMG_MUL  = [1.0, 2.0, 4.0, 8.0]
FLAT_MUL = [1.0, 2.0, 3.0, 5.0]   # shield/heal/poison/burn/bonuses
PCT_ADD  = [0, 5, 10, 20]         # crit / lifesteal percentage points added

def tiers(base):
    out = []
    for i in range(4):
        t = {
            "cost":       int(round(base["cost"] * COST_MUL[i])),
            "cooldown":   base["cooldown"],
            "damage":     int(round(base.get("damage", 0) * DMG_MUL[i])),
            "crit":       (base.get("crit", 0) + PCT_ADD[i]) if base.get("crit", 0) else 0,
            "multicast":  base.get("multicast", 0) + (1 if base.get("multicast", 0) and i >= 2 else 0),
            "shield":     int(round(base.get("shield", 0) * FLAT_MUL[i])),
            "heal":       int(round(base.get("heal", 0) * FLAT_MUL[i])),
            "lifesteal":  (base.get("lifesteal", 0) + PCT_ADD[i]) if base.get("lifesteal", 0) else 0,
            "poison":     int(round(base.get("poison", 0) * FLAT_MUL[i])),
            "burn":       int(round(base.get("burn", 0) * FLAT_MUL[i])),
            "shock":      base.get("shock", 0) + (1 if base.get("shock", 0) and i == 3 else 0),
            "startOfCombat": None,
            "stormBonus": int(round(base.get("stormBonus", 0) * FLAT_MUL[i])),
            "flameBonus": int(round(base.get("flameBonus", 0) * FLAT_MUL[i])),
        }
        if base.get("socMul"):
            t["startOfCombat"] = {"damageMultiplier": round(1.0 + (base["socMul"] - 1.0) * FLAT_MUL[i], 4)}
        out.append(t)
    return out

ITEMS = [
    ("bone_club",     "Bone Club",      "Mace",    "Neutral", {"cost":15,"cooldown":3.0,"damage":25}),
    ("iron_sword",    "Iron Sword",     "Sword",   "Neutral", {"cost":18,"cooldown":2.5,"damage":22}),
    ("swift_dagger",  "Swift Dagger",   "Dagger",  "Neutral", {"cost":20,"cooldown":1.0,"damage":8,"multicast":1}),
    ("long_spear",    "Long Spear",     "Spear",   "Neutral", {"cost":22,"cooldown":3.5,"damage":34,"crit":10}),
    ("war_axe",       "War Axe",        "Axe",     "Neutral", {"cost":25,"cooldown":5.0,"damage":60}),
    ("hunters_bow",   "Hunter's Bow",   "Bow",     "Neutral", {"cost":20,"cooldown":2.0,"damage":16,"crit":15}),
    ("storm_bow",     "Storm Bow",      "Bow",     "Storm",   {"cost":24,"cooldown":2.0,"damage":14,"stormBonus":3}),
    ("thunder_rod",   "Thunder Rod",    "Spell",   "Storm",   {"cost":26,"cooldown":4.0,"damage":18,"shock":1,"stormBonus":2}),
    ("tempest_blade", "Tempest Blade",  "Sword",   "Storm",   {"cost":28,"cooldown":2.5,"damage":20,"stormBonus":4}),
    ("flame_brand",   "Flame Brand",    "Sword",   "Flame",   {"cost":24,"cooldown":3.0,"damage":20,"burn":3,"flameBonus":2}),
    ("ember_orb",     "Ember Orb",      "Artifact","Flame",   {"cost":22,"cooldown":4.0,"damage":0,"burn":6,"flameBonus":3}),
    ("pyre_staff",    "Pyre Staff",     "Spell",   "Flame",   {"cost":27,"cooldown":3.5,"damage":15,"burn":5}),
    ("frost_dagger",  "Frost Dagger",   "Dagger",  "Frosty",  {"cost":21,"cooldown":2.0,"damage":10,"shock":1}),
    ("glacier_shield","Glacier Shield", "Shield",  "Frosty",  {"cost":19,"cooldown":3.0,"shield":40}),
    ("holy_mace",     "Holy Mace",      "Mace",    "Holy",    {"cost":26,"cooldown":3.0,"damage":18,"heal":15}),
    ("sanctum_relic", "Sanctum Relic",  "Artifact","Holy",    {"cost":30,"cooldown":6.0,"damage":0,"heal":25,"socMul":1.15}),
    ("venom_kris",    "Venom Kris",     "Dagger",  "Neutral", {"cost":23,"cooldown":2.5,"damage":6,"poison":4}),
    ("tower_shield",  "Tower Shield",   "Shield",  "Neutral", {"cost":17,"cooldown":2.5,"shield":30}),
    ("bloodfang",     "Bloodfang",      "Axe",     "Neutral", {"cost":29,"cooldown":3.5,"damage":30,"lifesteal":10}),
    ("warhorn",       "Warhorn",        "Warfare", "Neutral", {"cost":25,"cooldown":8.0,"damage":0,"socMul":1.20}),
]

data = [{"id": i, "name": n, "type": t, "tag": g, "tiers": tiers(b)} for i, n, t, g, b in ITEMS]
out = os.path.join(os.path.dirname(__file__), "..", "seed", "data", "items.json")
with open(os.path.abspath(out), "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print(f"wrote {len(data)} items -> {os.path.abspath(out)}")

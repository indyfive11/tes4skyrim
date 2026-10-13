"""Invented exported records the add-on tests convert, and a subrecord splitter."""

import struct
import zlib

NPC = {'Signature': 'NPC_', 'FormID': '00000500', 'RecordFlags': '0', 'EditorID': 'TestFencer',
       'ACBS.Flags': '0', 'ACBS.Level': '5', 'ACBS.CalcMin': '5', 'ACBS.CalcMax': '10',
       'FactionCount': '0', 'SpellCount': '1', 'Spell[0]': '00000A11', 'ItemCount': '1',
       'Item[0].FormID': '00000A12', 'Item[0].Count': '1', 'AIPackageCount': '0',
       'DATA.Health': '50', 'DATA.Strength': '60', 'DATA.Agility': '45',
       'HCLR.R': '100', 'HCLR.G': '80', 'HCLR.B': '60'}

CREA = {'Signature': 'CREA', 'FormID': '00000501', 'RecordFlags': '0', 'EditorID': 'TestBoar',
        'SpellCount': '1', 'Spell[0]': '00000A11', 'FactionCount': '0', 'ItemCount': '1',
        'Item[0].FormID': '00000A12', 'Item[0].Count': '1', 'AIPackageCount': '0',
        'ACBS.Flags': '0', 'ACBS.Level': '3', 'DATA.Health': '40', 'DATA.Strength': '70'}

#: (TES4 weapon type, mesh, the Skyrim animation type the converter gives it).
WEAPONS = {
    'sword': (0, 'Weapons\\Iron\\LongSword.NIF', 1),
    'dagger': (0, 'Weapons\\Iron\\Dagger.NIF', 2),
    'war axe': (2, 'Weapons\\Iron\\WarAxe.NIF', 3),
    'mace': (2, 'Weapons\\Iron\\Mace.NIF', 4),
    'greatsword': (1, 'Weapons\\Iron\\Claymore.NIF', 5),
    'battleaxe': (3, 'Weapons\\Iron\\BattleAxe.NIF', 6),
    'warhammer': (3, 'Weapons\\Iron\\Warhammer.NIF', 6),
    'bow': (5, 'Weapons\\Iron\\Bow.NIF', 7),
    'staff': (4, 'Weapons\\Staff\\Staff.NIF', 8),
}

#: TES4 effect code -> (name, TES4 DATA.Flags) of an invented MGEF: five attribute families, a skill one, a fixed one.
EFFECTS = {'FOAT': ('Fortify Attribute', 0x00100012), 'DRAT': ('Drain Attribute', 0x00100017),
           'DGAT': ('Damage Attribute', 0x00100015), 'REAT': ('Restore Attribute', 0x00100010),
           'ABAT': ('Absorb Attribute', 0x00100017), 'FOSK': ('Fortify Skill', 0x00080012),
           'FOHE': ('Fortify Health', 0x00000012)}

#: The TES4 actor value an entry of each code that is no attribute effect names: Blade, and none.
OTHER_ENTRIES = (('FOSK', 14), ('FOHE', -1))


def mgef(code: str, **fields) -> dict:
    """An exported MGEF of TES4 effect code `code`, carrying a hit shader; `fields` are added."""
    name, flags = EFFECTS[code]
    return {'Signature': 'MGEF', 'FormID': f'{0x1000 + sorted(EFFECTS).index(code):08X}', 'EditorID': code,
            'FULL': name, 'DESC': f'{name} of an invented plugin.', 'DATA.School': '5',
            'DATA.Flags': str(flags), 'DATA.BaseCost': '0.6', 'DATA.EffectShader': '00012345', **fields}


def effect_entry(code: str, actor_value: int) -> dict:
    """An exported item with one entry of `code` on TES4 actor value `actor_value`."""
    return {'Signature': 'SPEL', 'EffectCount': '1', 'Effect[0].EFID': code,
            'Effect[0].ActorValue': str(actor_value)}


class VariantWriter:
    """The writer calls the effect-variant builder makes."""

    def __init__(self):
        """Start with nothing written."""
        self.records = []

    def derive_formid(self, site, key):
        """A FormID of this plugin that is a function of (site, key) alone."""
        return zlib.crc32(f'{site}|{key!r}'.encode()) & 0x00FFFFFF

    def add_record(self, sig, data):
        """Keep each written record."""
        self.records.append((sig, data))


def weap(tes4_type: int, model: str) -> dict:
    """An exported WEAP of TES4 type `tes4_type` with mesh `model`."""
    return {'Signature': 'WEAP', 'FormID': '00012345', 'EditorID': 'TestWeapon',
            'RecordFlags': '0', 'FULL': 'Test Weapon', 'Model.MODL': model,
            'DATA.Type': str(tes4_type), 'DATA.Speed': '0.8', 'DATA.Reach': '1.3',
            'DATA.Value': '10', 'DATA.Weight': '5.0', 'DATA.Damage': '7'}


def subrecords(record: bytes) -> list:
    """[(signature, payload)] of a packed record's subrecords."""
    out, pos = [], 24
    while pos + 6 <= len(record):
        size = struct.unpack_from('<H', record, pos + 4)[0]
        out.append((record[pos:pos + 4].decode(), record[pos + 6:pos + 6 + size]))
        pos += 6 + size
    return out

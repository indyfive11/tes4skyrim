"""A TES4 source's character-sheet tables, in the Morrowind sidecar's formats.

See: docs/commentary/morrowind_runtime.md#tes4-tables
"""

import struct

from core import birthsign_text, chargen_source
from script_convert import context_setup, message_menus
from tes4_export.record_types.morrowind import MW_SKILL_TO_TES4
from tes5_import.actors import attribute_tables as tables
from tes5_import.actors import misc_stats, stat_factions
from tes5_import.base import conditions, owned_records
from tes5_import.record_types import crime


def _race(fid: str, playable: int, male: int, female: int) -> dict:
    """A RACE export record with every attribute at `male` / `female`."""
    rec = {'FormID': fid, 'EditorID': f'Race{fid}', 'DATA.Flags': str(playable)}
    for name in owned_records.TES4_ATTRIBUTE_NAMES:
        rec[f'ATTR.Male.{name}'] = str(male)
        rec[f'ATTR.Female.{name}'] = str(female)
    return rec


def test_namesakes_agree_with_the_morrowind_fold():
    """Each TES4 skill's namesake folds back into that same TES4 skill."""
    assert len(tables.TES4_SKILL_TO_MW) == 21
    assert len(set(tables.TES4_SKILL_TO_MW.values())) == 21
    for tes4, mw in tables.TES4_SKILL_TO_MW.items():
        assert MW_SKILL_TO_TES4[mw] == tes4


def test_skill_row_is_keyed_by_the_namesake():
    """Oblivion's Blade (actor value 14) is Long Blade's row, 5."""
    blade = {'DATA.Action': '14', 'DATA.Attribute': '0', 'DATA.Specialization': '0',
             'DATA.UseValue1': '1.5', 'DATA.UseValue2': '0.0'}
    specs, lines = tables.skill_lines([blade])
    assert lines == ['5=0|0|1.5,0'] and specs == {2: 0}


def test_playable_race_lands_on_its_skyrim_race_and_vampire():
    """Dark Elf keys DarkElfRace and its vampire; Dremora is skipped."""
    lines = tables.race_lines([_race('000191C1', 1, 40, 30), _race('00038010', 0, 50, 50)])
    assert lines == ['00013742=Race000191C1|40,40,40,40,40,40,40,40|30,30,30,30,30,30,30,30',
                     '0008883D=Race000191C1|40,40,40,40,40,40,40,40|30,30,30,30,30,30,30,30']


def test_player_row_carries_its_authored_attributes():
    """Oblivion's Player record is the start the race moves."""
    player = {'EditorID': 'Player', 'FULL': 'Bendu Olo', 'RNAM.Race': '00000907',
              'ACBS.Flags': '0', 'ACBS.Level': '1', 'DATA.Strength': '50',
              'DATA.Luck': '50', 'DATA.Personality': '50', 'DATA.Speechcraft': '5'}
    line, = tables.actor_lines([player], [], {})
    key, value = line.split('=', 1)
    fields = value.split('|')
    assert key == 'Player' and fields[0] == 'Imperial' and fields[6] == 'Bendu Olo'
    assert fields[19] == '50,0,0,0,0,0,50,50'
    assert fields[20].split(',')[25] == '5'


def test_only_the_plugins_own_globals_are_listed(monkeypatch):
    """A global adopted from a master is the master's sidecar's row."""
    monkeypatch.setitem(owned_records.WELL_KNOWN_PROPERTIES, 'TES4PlayerStrength', 0x01000A01)
    monkeypatch.setitem(owned_records.WELL_KNOWN_PROPERTIES, 'TES4PlayerLuck', 0x00000A08)
    assert tables.global_lines('Mod.esp', ['Oblivion.esm']) == ['strength=Mod.esp|01000A01']


class _Writer:
    """Just enough PluginWriter for `_emit_global`."""

    def __init__(self):
        self.added = []

    def derive_formid(self, sig: str, edid: str) -> int:
        """A stand-in id per EditorID."""
        return 0x01000000 + len(self.added)

    def add_record(self, sig: str, data: bytes) -> None:
        """Keep what was written."""
        self.added.append(data)


def test_missing_globals_are_created_in_a_dependent(monkeypatch):
    """A master built before the attribute globals existed leaves them to the plugin."""
    monkeypatch.setattr(owned_records, 'WELL_KNOWN_PROPERTIES', {'TES4Fame': 1, 'TES4Infamy': 2,
                                                                 'TES4GoldFenced': 3,
                                                                 'TES4ControlsDisabled': 4})
    writer = _Writer()
    assert owned_records.create_missing_globals(writer) == 8
    assert all(edid in owned_records.WELL_KNOWN_PROPERTIES
               for edid in owned_records.PLAYER_ATTRIBUTE_GLOBALS)


class _Masters:
    """A master index holding only Strength's faction."""

    def find_by_edid(self, sig: bytes, edid: str) -> int:
        """Strength's faction is the master's."""
        return 0x00000F01 if (sig, edid) == (b'FACT', 'TES4AttributeStrength') else 0


def test_stat_factions_adopt_a_masters_and_rank_actors():
    """Eleven factions, a master's adopted; an NPC joins all at its values, capped at 127."""
    writer = _Writer()
    made = stat_factions.create_stat_factions(writer, _Masters(), wanted=True)
    assert len(made) == 11 and made['TES4AttributeStrength'] == 0x00000F01
    assert len(writer.added) == 10
    npc = {'DATA.Strength': '255', 'DATA.Luck': '40', 'DATA.Athletics': '35'}
    ranks = dict(stat_factions.stat_memberships(npc))
    assert ranks[0x00000F01] == 127 and ranks[made['TES4AttributeLuck']] == 40
    assert ranks[made['TES4SkillAthletics']] == 35
    assert len(stat_factions.stat_memberships(npc, creature=True)) == 8
    assert stat_factions.faction_rows('Mod.esp', ['Oblivion.esm'])[0] == (
        'faction.0=Oblivion.esm|00000F01')
    stat_factions.create_stat_factions(writer, None, wanted=False)
    assert stat_factions.stat_memberships(npc) == []


def test_birthsign_pictures_key_by_sign():
    """Morrowind's shortened file names and Oblivion's long ones key the same sign."""
    assert message_menus.birthsign_key('birthsigns\\tx_birth_apprent.dds') == 'apprentice'
    assert message_menus.birthsign_key('Menus\\BirthSign\\Birthsign_The Lady.dds') == 'lady'


def test_chargen_rows_follow_the_menu_order():
    """A class's rows are its later record's data; a sign lists its spells by name."""
    signs = [{'FormID': '00000A01', 'FULL': 'The Lady', 'ICON': 'birthsign_the lady.dds',
              'DESC': 'Charge', 'Spell[0]': '00000B01'}]
    classes = [{'FormID': '00000C01', 'FULL': 'Mage', 'DATA.Flags': '1',
                'DATA.Specialization': '1', 'DATA.PrimaryAttribute1': '1',
                'DATA.PrimaryAttribute2': '2'},
               {'FormID': '01000C02', 'FULL': 'Mage', 'DATA.Flags': '1',
                'DATA.Specialization': '1', 'DATA.PrimaryAttribute1': '2',
                'DATA.PrimaryAttribute2': '7'},
               {'FormID': '00000C03', 'FULL': 'Agent', 'DATA.Flags': '1'}]
    spells = [{'FormID': '00000B01', 'EditorID': 'LadyFavor', 'FULL': "Lady's Favor"}]
    plan = message_menus.build_chargen_menus(signs, classes, spells)
    assert [row['name'] for row in plan['class']['rows']] == ['Agent', 'Mage']
    assert plan['class']['rows'][1]['attributes'] == (2, 7)
    assert plan['class']['fid_to_index'] == {0xC01: 1, 0xC02: 1, 0xC03: 0}
    assert plan['birthsign']['rows'][0]['spells'] == ["Lady's Favor"]
    assert plan['birthsign']['actions'] == [['LadyFavor']]


def test_a_later_sign_of_the_same_name_wins():
    """Morroblivion's patch: Morrowind's Lady takes Oblivion's slot, not a second one."""
    signs = [{'FormID': '00000A01', 'FULL': 'The Lady', 'DESC': 'Oblivion'},
             {'FormID': '00000A02', 'FULL': 'The Lord', 'DESC': 'Oblivion'},
             {'FormID': '02000A03', 'FULL': 'The Lady', 'DESC': 'Morrowind'}]
    plan = message_menus.build_chargen_menus(signs, [], [])['birthsign']
    assert [(row['name'], row['desc']) for row in plan['rows']] == [
        ('The Lady', 'Morrowind'), ('The Lord', 'Oblivion')]
    assert plan['fid_to_index'] == {0xA01: 0, 0xA02: 1, 0xA03: 0}


def test_chargen_table_names_its_globals(monkeypatch):
    """A plugin's chargen.txt: its globals, then only its own names by menu index."""
    monkeypatch.setitem(owned_records.WELL_KNOWN_PROPERTIES, 'TES4ChargenRequest', 0x01000D01)
    monkeypatch.setitem(owned_records.WELL_KNOWN_PROPERTIES, 'TES4ChargenClassChoice', 0x00000D02)
    plan = {'class': {'rows': [{'name': 'Mage|x', 'spec': 1, 'attributes': (1, 2),
                                'desc': 'Line\nTwo|x'}]}}
    lines = tables.chargen_lines(plan, 'Mod.esp', ['Oblivion.esm'])
    assert 'form.request=Mod.esp|01000D01' in lines
    assert 'form.class=Oblivion.esm|00000D02' in lines
    assert 'class.0=Mage/x' in lines


def test_shared_rows_name_each_spell_by_its_owner():
    """The shared table's rows: a class whole, a TES4 sign's spell as Owner@FormID
    read off the sign's own masters, a TES3 sign's by its TES3 id."""
    spells = [{'FormID': '00000B01', 'EditorID': 'LadyFavor', 'FULL': "Lady's Favor"},
              {'FormID': '00000B02', 'EditorID': 'Grace', 'FULL': 'Grace'}]
    signs = [{'FormID': '01000A01', 'FULL': 'The Lady', 'ICON': 'birthsign_the lady.dds',
              'DESC': 'Charge', 'Spell[0]': '00000B01', 'Spell[1]': '01000B02',
              '_plugin': 'Mod.esp', '_masters': ['Master.esm']},
             {'FormID': '00000A02', 'FULL': 'The Lord', 'Spell[0]': '00000B01',
              'SpellId[0]': 'lords mail', '_plugin': 'Morrowind.esm', '_masters': []}]
    classes = [{'FormID': '00000C01', 'FULL': 'Mage', 'DATA.Flags': '1', 'DESC': 'Line|x',
                'DATA.Specialization': '1', 'DATA.PrimaryAttribute1': '1',
                'DATA.PrimaryAttribute2': '2'}]
    plan = message_menus.build_chargen_menus(signs, classes, spells)
    assert message_menus.class_line(0, plan['class']['rows'][0]) == 'class.0=Mage|1|1,2|Line/x'
    lady, lord = (message_menus.sign_line(i, row)
                  for i, row in enumerate(plan['birthsign']['rows']))
    assert lady == ("sign.0=The Lady|lady|Charge|Lady's Favor;Grace|"
                    'Master.esm@00000B01;Mod.esp@00000B02|')
    assert lord.endswith('|lords mail|')


def test_chargen_records_merge_the_masters(tmp_path):
    """A master's classes join the plugin's; its own copy of a record overrides."""
    master, plugin = tmp_path / 'Master.esm', tmp_path / 'Mod.esp'
    for folder, header, body in ((master, '', 'FormID=00000C01\nFULL=Old\nDATA.Flags=1\n'),
                                 (plugin, 'Master[0]=Master.esm\n',
                                  'FormID=00000C01\nFULL=New\nDATA.Flags=1\n')):
        folder.mkdir()
        (folder / '_HEADER.txt').write_text(header)
        (folder / 'CLAS.txt').write_text('---RECORD_BEGIN---\nSignature=CLAS\n' + body
                                         + '---RECORD_END---\n')
    records = context_setup.chargen_records(str(plugin))
    assert [rec['FULL'] for rec in records['CLAS']] == ['New']
    assert [rec['FULL'] for rec in records['own:CLAS']] == ['New']


def _export(root, name: str, files: dict) -> None:
    """One exported plugin folder holding `files` as {type: record body}."""
    folder = root / name
    folder.mkdir()
    (folder / '_HEADER.txt').write_text('')
    for sig, body in files.items():
        (folder / f'{sig}.txt').write_text(f'---RECORD_BEGIN---\nSignature={sig}\n{body}'
                                           '---RECORD_END---\n')


def test_one_shared_table_from_the_chosen_game(tmp_path):
    """The chosen game's classes; a menu it lacks comes from the next game; a
    Fallout export is never offered."""
    _export(tmp_path, 'A.esm', {'CLAS': 'FormID=00000C01\nFULL=Mage\nDATA.Flags=1\n'})
    _export(tmp_path, 'B.esm', {'BSGN': 'FormID=00000A01\nFULL=The Lady\n',
                                'CLAS': 'FormID=00000C01\nFULL=Rogue\nDATA.Flags=1\n'})
    _export(tmp_path, 'C.esm', {'BSGN': 'FormID=00000A01\nFULL=The Lord\n',
                                'CLAS': 'FormID=00000C01\nFULL=Monk\nDATA.Flags=1\n'
                                        'DATA.PrimaryAttribute1=3\nDATA.PrimaryAttribute2=4\n'})
    _export(tmp_path, 'F.esm', {'CLAS': 'FormID=00000C01\nFULL=Gun\nDATA.Flags=1\n',
                                'TERM': 'FormID=00000D01\n'})
    assert chargen_source.candidates(tmp_path) == ['A.esm', 'B.esm', 'C.esm']
    source, lines, _icons = chargen_source.shared_lines(tmp_path, 'a.esm')
    assert source == 'A.esm'
    assert [line.split('|')[0] for line in lines] == ['class.0=Mage', 'sign.0=The Lady']
    assert chargen_source.shared_lines(tmp_path, 'Gone.esm')[0] == 'C.esm', \
        'unset, the first game whose table is complete'


def test_a_sign_reads_as_openmw_writes_it():
    """Abilities first with no duration or range; a power's effect has both; an
    attribute effect names the attribute; each effect carries its icon.

    See: docs/commentary/morrowind_runtime.md#chargen-menus
    """
    mgefs = {'FOAT': {'FULL': 'Fortify Attribute', 'DATA.Flags': str(0x100000),
                      'ICON': 'Magic\\fortify.dds'},
             'REHE': {'FULL': 'Restore Health', 'DATA.Flags': '0', 'ICON': ''}}
    ability = {'FULL': 'Lady', 'SPIT.Type': '4', 'EffectCount': '1', 'Effect[0].EFID': 'FOAT',
               'Effect[0].Magnitude': '10', 'Effect[0].ActorValue': '6', 'Effect[0].Type': 'Self'}
    power = {'FULL': 'Blood', 'SPIT.Type': '2', 'EffectCount': '1', 'Effect[0].EFID': 'REHE',
             'Effect[0].Magnitude': '6', 'Effect[0].Duration': '15', 'Effect[0].Type': 'Self'}
    spells = [birthsign_text.tes4_spell(s, mgefs) for s in (power, ability)]
    assert birthsign_text.sign_lines(spells, birthsign_text.WORDS) == [
        ('h', '', 'Abilities:'), ('s', '', 'Lady'),
        ('e', 'textures\\menus\\icons\\Magic\\fortify.dds', 'Fortify Personality 10 pts'),
        ('h', '', 'Powers'), ('s', '', 'Blood'),
        ('e', '', 'Restore Health 6 pts for 15 secs on Self')]


def test_a_morrowind_effect_takes_openmw_units():
    """A range, a percentage and Fortify Maximum Magicka's multiple of INT."""
    words = birthsign_text.WORDS
    base = {'name': 'X', 'duration': 0, 'area': 0, 'range': 0, 'applied_once': False,
            'no_duration': False}
    span = dict(base, low=1, high=10, display=birthsign_text.POINTS)
    assert birthsign_text.effect_text(span, words, True) == 'X 1 to 10 pts'
    weak = dict(base, low=100, high=100, display=birthsign_text.PERCENT)
    assert birthsign_text.effect_text(weak, words, True) == 'X 100%'
    times = dict(base, low=15, high=15, display=birthsign_text.TIMES_INT)
    assert birthsign_text.effect_text(times, words, True) == 'X 1.5x INT'
    assert birthsign_text.mw_effect_name(79, -1, 6, {}) == 'Fortify Personality'


def test_stat_rows_name_the_written_stats(monkeypatch):
    """A written misc stat is a row under its setting and default; the labels ride along."""
    monkeypatch.setitem(owned_records.WELL_KNOWN_PROPERTIES, 'TES4MiscStat22', 0x00000E16)
    by_type = {'SCPT': [{'SCTX': 'ModPCMiscStat 22 EPdiff\nset x to GetPCMiscStat 3'}],
               'GMST': [{'EditorID': 'sMiscDaysAsAVampire', 'DATA.Value': 'EP: '}]}
    assert misc_stats.misc_stat_calls(by_type) == ({3, 22}, {22})
    rows = misc_stats.stat_lines(by_type, 'Nehrim.esm', [])
    assert 'misc.22=sMiscDaysAsAVampire|Days As A Vampire|Nehrim.esm|00000E16' in rows
    assert 'label.sMiscDaysAsAVampire=EP' in rows
    assert not misc_stats.is_script_kept(14), 'Horses Owned is Skyrim\'s own stat'
    assert misc_stats.is_script_kept(19), 'Artifacts Found has no Skyrim stat'


def test_only_a_written_stat_is_listed_and_a_long_label_is_shortened(monkeypatch):
    """Closing a gate writes Gates Shut, as Oblivion.exe's own command did; a stat
    nothing writes (Picks Broken) is not listed; Nehrim's long label is short."""
    for index in (9, 13):
        monkeypatch.setitem(owned_records.WELL_KNOWN_PROPERTIES, f'TES4MiscStat{index:02d}',
                            0x01000E00 + index)
    by_type = {'SCPT': [{'SCTX': 'CloseCurrentOblivionGate 1\nset x to GetPCMiscStat 9'}],
               'GMST': [{'EditorID': 'sMiscDaysAsAVampire',
                         'DATA.Value': 'Overall amount of experience points'}]}
    rows = misc_stats.stat_lines(by_type, 'Oblivion.esm', ['Skyrim.esm'])
    assert [r for r in rows if r.startswith('misc.')] == [
        'misc.13=sMiscOblivionGatesShut|Oblivion Gates Shut|Oblivion.esm|01000E0D']
    assert 'label.sMiscDaysAsAVampire=Total XP' in rows


def test_the_page_rows_mirror_the_bank_and_its_interest(monkeypatch):
    """The bank variable's mirror and the interest's stage rule are page rows."""
    monkeypatch.setitem(owned_records.WELL_KNOWN_PROPERTIES,
                        'TES4PageStat_ErothinBankQuest_PlayerKontostand', 0x01000F01)
    monkeypatch.setattr(misc_stats, 'remap_formid', lambda fid: fid + 0x01000000)
    by_type = {'QUST': [{'FormID': '00000101', 'EditorID': 'MQ14'},
                        {'FormID': '00000102', 'EditorID': 'MQ19'}],
               'SCPT': [{'SCTX': 'Set ErothinBankQuest.PlayerKontostand to 5'}]}
    assert misc_stats.page_writes(by_type) == [misc_stats.PAGE_VARIABLES[0]]
    rows = misc_stats.stat_lines(by_type, 'Nehrim.esm', ['Skyrim.esm'])
    assert 'page.0=Bank balance|Nehrim.esm|01000F01' in rows
    assert ('page.1=Bank interest (percent)|Nehrim.esm@01000101,20,2;'
            'Nehrim.esm@01000102,70,1|3') in rows


def test_bounty_rows_list_the_owned_realms_main_first(monkeypatch):
    """Each realm the plugin owns is a bounty row under its name, main first."""
    monkeypatch.setattr(crime, '_OWN_REALMS', [(0x01000A01, 'Cyrodiil'), (0x01000A02, 'Isles|x')])
    assert crime.bounty_rows('Oblivion.esm', ['Skyrim.esm']) == [
        'bounty.0=Cyrodiil|Oblivion.esm|01000A01', 'bounty.1=Isles/x|Oblivion.esm|01000A02']


def _ctda(func: int, av: int, run_on_target: bool) -> bytes:
    """A TES4 CTDA: `func(av) >= 50`, on the subject or the target."""
    return struct.pack('<B3xfHH4xII', 0x60 | (2 if run_on_target else 0), 50.0, func, 0, av, 0)


def test_npc_attribute_condition_reads_its_stat_faction():
    """An NPC-subject GetActorValue Strength is GetFactionRank on its faction."""
    stat_factions.create_stat_factions(_Writer(), None, wanted=True)
    faction = stat_factions.stat_faction(0)
    out = conditions.convert_ctda(_ctda(14, 0, False), offset=0)
    func, param1 = struct.unpack_from('<H2xI', out, 8)
    assert (func, param1) == (conditions.FUNC_GET_FACTION_RANK, faction)
    stat_factions.create_stat_factions(_Writer(), None, wanted=False)

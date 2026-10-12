"""Which hair variants a plugin bakes, and which one each NPC wears.

One plan, read by both stages: hair_pipeline bakes a mesh per variant, the
import stage emits an HDPT per variant and points each NPC_ at one, so the two
cannot disagree.  A variant is (length step, gender, family); the family is the
head it is fitted to, taken from the WEARER's race.  Baked: what NPCs wear, each
hair's base variant, and the length-0 variants playable races' authored hair
lists offer.  A dependent bakes only what its master's plan lacks.

See: docs/commentary/asset_convert_armor.md#hair-variants-follow-the-wearer
"""

import os
from collections import Counter, namedtuple
from functools import lru_cache

from core.plugin_masters import master_dirs
from asset_convert.character.head_fit import OB_HEAD_MESH, fit_race_for_hair
from asset_convert.game_paths import current_namespace
from output_layout import assets_for
from tes5_import.base.equivalents import TES4_RACE_FID_TO_EDID
from tes5_import.base.race_lookup import stand_ins

#: Steps an authored LNAM is quantized to; changing it renumbers every non-zero variant (FormID drift).
LENGTH_BUCKETS = 4

#: Oblivion race EditorID -> the head family its hair is fitted to; unlisted races wear the human head.
RACE_FAMILY = {'HighElf': 'elves', 'WoodElf': 'elves', 'DarkElf': 'elves',
               'GoldenSaint': 'elves', 'DarkSeducer': 'elves', 'Orc': 'orc',
               'Dremora': 'dremora', 'SEDremora': 'dremora',
               'Khajiit': 'khajiit', 'Argonian': 'argonian'}

#: Head family -> the head_fit (race pack, group) its meshes are fitted with.
FIT_ARGS = {'human': (None, None), 'elves': (None, 'elves'), 'orc': (None, 'orc'),
            'khajiit': ('khajiit', None), 'argonian': ('argonian', None)}

#: Mesh filename suffix of a family's variant when it is not the hair's home family.
FAMILY_SUFFIX = {'human': '__hu', 'elves': '__ev', 'orc': '__or',
                 'khajiit': '__kh', 'argonian': '__ar'}

#: EditorID suffix of a family's variant when it is not the hair's home family.
FAMILY_EDID = {'human': 'Hum', 'dremora': 'Dre', 'elves': 'Elf', 'orc': 'Orc',
               'khajiit': 'Kha', 'argonian': 'Arg'}

#: FormID-key tag of a family's variant when it is not the hair's home family.
FAMILY_TAG = {'human': 'H', 'dremora': 'D', 'elves': 'E', 'orc': 'O',
              'khajiit': 'K', 'argonian': 'A'}

#: TES4 HAIR DATA flag bits.
HAIR_PLAYABLE, HAIR_NOT_MALE, HAIR_NOT_FEMALE = 0x01, 0x02, 0x04

#: Mesh-relative path of the head the shipped fit was built from; any other head needs its own pack.
FIT_HEAD = 'characters/' + OB_HEAD_MESH.as_posix()

Variant = namedtuple('Variant', 'bucket female family')
Race = namedtuple('Race', 'family playable hairs heads')
Plan = namedtuple('Plan', 'hairs heads')


# ---------------------------------------------------------------------------
# Lengths, names and paths
# ---------------------------------------------------------------------------

def quantize_length(value) -> int:
    """Authored LNAM float -> step in [0, LENGTH_BUCKETS], clamped; junk is 0."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if not v > 0.0:
        return 0
    return LENGTH_BUCKETS if v >= 1.0 else int(round(v * LENGTH_BUCKETS))


def bucket_weight(bucket: int) -> float:
    """Length step -> the HairMorph blend weight it bakes."""
    return min(max(bucket, 0), LENGTH_BUCKETS) / float(LENGTH_BUCKETS)


def mesh_family(family: str) -> str:
    """The family whose mesh a variant wears; Dremora share the human scalp."""
    return 'human' if family == 'dremora' else family


def variant_stem(stem: str, bucket: int, female: bool = False, family=None) -> str:
    """Output stem of a variant: `__f`, FAMILY_SUFFIX (None = home), `__lNN`."""
    stem = stem + ('__f' if female else '') + FAMILY_SUFFIX.get(family, '')
    return stem if bucket <= 0 else '%s__l%02d' % (stem, bucket)


def variant_edid(edid: str, bucket: int, female: bool = False, family=None) -> str:
    """EditorID of a variant's HDPT; `family` is None for the home family."""
    out = 'TES4Hair%s%s%s' % (edid or 'Hair', 'F' if female else '',
                              FAMILY_EDID.get(family, ''))
    return out if bucket <= 0 else '%s_L%02d' % (out, bucket)


def out_rel_dir() -> str:
    """Hair output folder, under the ACTIVE game namespace."""
    return os.path.join(current_namespace(), 'characters', 'hair')


def norm_model(path: str) -> str:
    """MODL path -> lowercase mesh-relative path with forward slashes."""
    p = (path or '').strip().replace('\\\\', '\\').replace('\\', '/')
    return p.lstrip('/').lower()


def output_model_path(model: str, bucket: int, female: bool = False,
                      family=None) -> str:
    """The MODL path a variant's HDPT carries (mesh-relative, no namespace)."""
    stem = os.path.splitext(os.path.basename(norm_model(model)))[0]
    rel = out_rel_dir().replace(os.sep, '\\').split('\\', 1)[1]
    return '%s\\%s.nif' % (rel, variant_stem(stem, bucket, female, family))


def output_tri_path(model: str, bucket: int, female: bool = False,
                    family=None) -> str:
    """The NAM1 (.tri) path a variant's HDPT carries."""
    return os.path.splitext(
        output_model_path(model, bucket, female, family))[0] + '.tri'


def source_tri_exists(export_dir, model: str) -> bool:
    """True when the source hair ships the .tri its length morph and NAM1 come from."""
    rel = norm_model(model)
    if not rel or not export_dir:
        return False
    src = os.path.join(str(assets_for(export_dir)), 'meshes', *rel.split('/'))
    return os.path.isfile(os.path.splitext(src)[0] + '.tri')


# ---------------------------------------------------------------------------
# Hair records
# ---------------------------------------------------------------------------

def fit_group_lock(edid: str):
    """The race group a race-NAMED hair's EditorID names ('elves'/'orc'/'humans'), else None."""
    low = (edid or '').lower()
    if 'elf' in low:
        return 'elves'
    if 'orc' in low:
        return 'orc'
    if any(t in low for t in ('nord', 'imperial', 'breton', 'redguard',
                              'dremora')):
        return 'humans'
    return None


def home_family(edid: str) -> str:
    """Family a hair's unsuffixed variants fit: its named race, else human."""
    lock = fit_race_for_hair(edid) or fit_group_lock(edid)
    return 'human' if lock in (None, 'humans') else lock


def is_generic(edid: str) -> bool:
    """True when the EditorID names no race (its home RNAM is the human list)."""
    return fit_race_for_hair(edid) is None and fit_group_lock(edid) is None


def hair_genders(data_flags: int) -> tuple:
    """Genders a TES4 HAIR allows, as female-bools; excluding both reads as unisex."""
    out = tuple(f for f, bit in ((False, HAIR_NOT_MALE), (True, HAIR_NOT_FEMALE))
                if not data_flags & bit)
    return out or (False, True)


def hair_entry(rec: dict, owner) -> dict:
    """A plan entry for one HAIR record owned by the export at `owner` (may be None)."""
    edid = (rec.get('EditorID') or '').strip()
    model = (rec.get('Model.MODL') or '').strip()
    flags = _int(rec, 'DATA.Flags')
    genders = hair_genders(flags)
    home = home_family(edid)
    return {'edid': edid, 'model': model, 'genders': genders,
            'playable': bool(flags & HAIR_PLAYABLE), 'home': home,
            'generic': is_generic(edid), 'owner': owner,
            'has_tri': source_tri_exists(owner, model),
            'base': Variant(0, genders[0], home), 'variants': set(),
            'master_variants': set()}


def wearer_variant(entry: dict, lnam, female: bool, family: str) -> Variant:
    """The variant an NPC wears; lengths collapse to 0 on a hair with no .tri morph."""
    genders = entry['genders']
    bucket = quantize_length(lnam) if entry['has_tri'] else 0
    return Variant(bucket, female if female in genders else genders[0], family)


def variant_tag(v: Variant, entry: dict) -> str:
    """FormID-key tag of a variant: '' for the home family, else FAMILY_TAG."""
    return '' if v.family == entry['home'] else FAMILY_TAG[v.family]


def mesh_name_family(v: Variant, entry: dict):
    """The family naming a variant's mesh file: None when it is the home family's."""
    fam = mesh_family(v.family)
    return None if fam == entry['home'] else fam


# ---------------------------------------------------------------------------
# Export text
# ---------------------------------------------------------------------------

def iter_records(txt):
    """Parsed records of an export .txt; a repeated key keeps its FIRST value."""
    if not os.path.isfile(txt):
        return
    with open(txt, 'r', encoding='utf-8', errors='replace') as fh:
        body = fh.read()
    for chunk in body.split('---RECORD_BEGIN---')[1:]:
        rec = {}
        for line in chunk.split('---RECORD_END---')[0].splitlines():
            k, sep, v = line.strip().partition('=')
            if sep and k and not k.startswith('#'):
                rec.setdefault(k, v)
        if rec:
            yield rec


def _fid(rec: dict, key: str) -> int:
    """Low 24 bits of a FormID field, 0 when absent or unreadable."""
    try:
        return int((rec.get(key) or '0').split()[0], 16) & 0x00FFFFFF
    except (ValueError, IndexError):
        return 0


def _int(rec: dict, key: str) -> int:
    """An integer field, 0 when absent or unreadable."""
    try:
        return int((rec.get(key) or '0').split()[0])
    except (ValueError, IndexError):
        return 0


# ---------------------------------------------------------------------------
# Races
# ---------------------------------------------------------------------------

def _race_heads(rec: dict) -> tuple:
    """(male, female) mesh-relative head paths a RACE authors; '' when absent."""
    return tuple(norm_model(rec.get(k) or rec.get('FacePart[0].Model') or '')
                 for k in ('MalePart[0].Model', 'FemalePart[0].Model'))


def race_table(dirs) -> dict:
    """{race FormID: Race} over the exports in `dirs`, later ones overriding."""
    recs = [(d, r) for d in dirs
            for r in iter_records(os.path.join(str(d), 'RACE.txt'))]
    stand = stand_ins([r for _d, r in recs])
    out = {}
    for d, r in recs:
        fid = _fid(r, 'FormID')
        edid = TES4_RACE_FID_TO_EDID.get(fid) or stand.get(fid) or 'Imperial'
        heads = tuple((d, h) if h else None for h in _race_heads(r))
        out[fid] = Race(RACE_FAMILY.get(edid, 'human'),
                        bool(_int(r, 'DATA.Flags') & 1),
                        tuple(_fid(r, k) for k in r if k.startswith('Hair[')),
                        heads)
    return out


def source_heads(races: dict) -> dict:
    """{female: absolute source-head path, or None where the shipped fit's head applies}."""
    out = {}
    for g, female in enumerate((False, True)):
        counts = Counter(r.heads[g] for r in races.values()
                         if r.playable and r.heads[g])
        out[female] = None
        if counts:
            d, rel = counts.most_common(1)[0][0]
            path = os.path.join(str(assets_for(d)), 'meshes', *rel.split('/'))
            if rel != FIT_HEAD and os.path.isfile(path):
                out[female] = path
    return out


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

def _needed(export_dir, hairs: dict, races: dict) -> dict:
    """{hair FormID: variants} this plugin's NPCs wear or its playable races offer."""
    need = {}
    for r in iter_records(os.path.join(str(export_dir), 'NPC_.txt')):
        h = _fid(r, 'HNAM.Hair')
        if h in hairs:
            race = races.get(_fid(r, 'RNAM.Race'))
            v = wearer_variant(hairs[h], r.get('LNAM.HairLength'),
                               bool(_int(r, 'ACBS.Flags') & 1),
                               race.family if race else 'human')
            need.setdefault(h, set()).add(v)
    for race in races.values():
        for h in race.hairs if race.playable else ():
            if h in hairs and hairs[h]['playable']:
                need.setdefault(h, set()).update(
                    Variant(0, f, race.family) for f in hairs[h]['genders'])
    return need


def _same_dir(a, b) -> bool:
    """True when two export folder paths name the same folder."""
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(
        os.path.abspath(str(b)))


@lru_cache(maxsize=None)
def _plan(export_dir: str) -> Plan:
    """The plan of one normalized export folder, computed once per process.

    `hairs` maps every hair in scope to its entry: a hair this plugin first
    defines emits all its `variants`, base included; a master's hair (or an
    override of one) only those the defining master's plan
    (`master_variants`) lacks.  `heads` is the per-gender source head (None =
    the shipped fit's).
    """
    scope = master_dirs(export_dir) + [export_dir]
    races = race_table(scope)
    hairs, origin = {}, {}
    for d in scope:
        for r in iter_records(os.path.join(d, 'HAIR.txt')):
            if _fid(r, 'FormID'):
                hairs[_fid(r, 'FormID')] = hair_entry(r, d)
                origin.setdefault(_fid(r, 'FormID'), d)
    need = _needed(export_dir, hairs, races)
    for fid, entry in hairs.items():
        wanted = need.get(fid, set())
        if _same_dir(origin[fid], export_dir):
            entry['variants'] = wanted | {entry['base']}
        else:
            first = build_plan(origin[fid]).hairs.get(fid)
            entry['master_variants'] = first['variants'] if first else set()
            entry['variants'] = wanted - entry['master_variants']
    return Plan(hairs, source_heads(races))


def build_plan(export_dir) -> Plan:
    """The hair plan of the plugin exported at `export_dir` (see _plan)."""
    return _plan(os.path.normcase(os.path.abspath(str(export_dir))))


def is_own(entry: dict, export_dir) -> bool:
    """True when the plugin exported at `export_dir` defines this hair."""
    return _same_dir(entry['owner'], export_dir)

"""
Render the runtime's menu movies to PNGs from their own tags, so a layout or a
menu style can be checked without the game.

It reads the subset the menu generators write -- DefineBitsLossless2, the
bitmap- and solid-rect DefineShape3, DefineSprite, PlaceObject2 and
DefineEditText -- and draws text in the embedded face (the vendored
MysticCards). A STATE stands in for the plugin: per instance name, `text`,
`x`, `y`, `visible`, `width`, `height` and `color`, as the plugin sets them.

    python tools/generators/menu_preview.py --out DIR [--style morrowind|skyrim] [--icons morrowind|oblivion]

writes every menu in sample states (`scenes`).

See: docs/commentary/morrowind_runtime.md#menu-previews
"""

import argparse
import os
import struct
import sys
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from asset_convert.ui.menu_art import ICON_SETS, STYLES, effect_icons, menu_art
from asset_convert.ui.skyrim_skills import skill_table_text
from asset_convert.ui.swf import TWIPS, BitReader
from tools.generators import gen_morrowind_chargen_swf as cg
from tools.generators import gen_morrowind_menu_swf as dlg
from tools.generators import gen_morrowind_stats_swf as st

#: The tags drawn; anything else (the font, the frame tags) is skipped.
TAG_BITS, TAG_SHAPE, TAG_SPRITE, TAG_PLACE, TAG_TEXT = 36, 32, 39, 26, 37

#: DefineEditText flags this reads: HasFont, HasTextColor (byte 1), HasLayout (byte 2).
HAS_FONT, HAS_COLOR, HAS_LAYOUT = 0x01, 0x04, 0x20

#: What the game shows behind a menu: a dark gray, so translucent panels read as such.
BACKDROP = (40, 44, 48, 255)


# ---------------------------------------------------------------------------
# Reading our own tags
# ---------------------------------------------------------------------------

def _rect(reader) -> tuple:
    """A RECT as `(x, y, w, h)` in pixels."""
    bits = reader.ub(5)
    x0, x1, y0, y1 = (reader.sb(bits) / TWIPS for _ in range(4))
    reader.align()
    return (x0, y0, x1 - x0, y1 - y0)


def _matrix(reader) -> tuple:
    """A MATRIX as `(scale_x, scale_y, x, y)`, rotation ignored."""
    sx = sy = 1.0
    if reader.ub(1):
        bits = reader.ub(5)
        sx, sy = reader.sb(bits) / 65536, reader.sb(bits) / 65536
    if reader.ub(1):
        bits = reader.ub(5)
        reader.sb(bits), reader.sb(bits)
    bits = reader.ub(5)
    x, y = reader.sb(bits) / TWIPS, reader.sb(bits) / TWIPS
    reader.align()
    return (sx, sy, x, y)


def _bitmap(data: bytes):
    """A DefineBitsLossless2's premultiplied ARGB as a straight RGBA image."""
    _cid, _fmt, w, h = struct.unpack_from('<HBHH', data)
    argb = np.frombuffer(zlib.decompress(data[7:]), np.uint8).reshape(h, w, 4).astype(np.float32)
    alpha = argb[..., :1]
    rgb = np.where(alpha > 0, argb[..., 1:] * 255.0 / np.maximum(alpha, 1), 0)
    return ('image', Image.fromarray(np.concatenate([rgb, alpha], 2).clip(0, 255)
                                     .astype(np.uint8), 'RGBA'))


def _shape(data: bytes) -> tuple:
    """A DefineShape3's fills: `('bitmap', id, x, y, sx, sy)` or `('solid', rgba, rect)`."""
    reader = BitReader(data, 2)
    bounds = _rect(reader)
    count, at = data[reader.offset], reader.offset + 1
    out = []
    for _ in range(count):
        if data[at] == 0x00:
            out.append(('solid', tuple(data[at + 1:at + 5]), bounds))
            at += 5
            continue
        bitmap = struct.unpack_from('<H', data, at + 1)[0]
        reader = BitReader(data, at + 3)
        sx, sy, x, y = _matrix(reader)
        out.append(('bitmap', bitmap, x, y, sx / TWIPS, sy / TWIPS))
        at = reader.offset
    return ('shape', out)


def _text(data: bytes) -> tuple:
    """A DefineEditText's rect, color, alignment and initial text."""
    reader = BitReader(data, 2)
    rect = _rect(reader)
    at = reader.offset
    flags1, flags2 = data[at], data[at + 1]
    at += 2
    rgb, align = (255, 255, 255), 0
    if flags1 & HAS_FONT:
        at += 4
    if flags1 & HAS_COLOR:
        rgb = tuple(data[at:at + 3])
        at += 4
    if flags2 & HAS_LAYOUT:
        align = data[at]
        at += 9
    at = data.index(b'\0', at) + 1
    initial = data[at:data.index(b'\0', at)].decode('latin1')
    return ('text', {'rect': rect, 'rgb': rgb, 'align': align, 'text': initial})


def _place(data: bytes) -> dict:
    """A PlaceObject2's depth, character, matrix and name."""
    flags, depth = data[0], struct.unpack_from('<H', data, 1)[0]
    at, char = 3, None
    if flags & 0x02:
        char = struct.unpack_from('<H', data, at)[0]
        at += 2
    matrix = (1.0, 1.0, 0.0, 0.0)
    if flags & 0x04:
        reader = BitReader(data, at)
        matrix = _matrix(reader)
        at = reader.offset
    name = data[at:data.index(b'\0', at)].decode('latin1') if flags & 0x20 else ''
    return {'depth': depth, 'char': char, 'matrix': matrix, 'name': name}


def _sprite(data: bytes) -> tuple:
    """A DefineSprite's placements."""
    out, at = [], 4
    while at < len(data):
        head = struct.unpack_from('<H', data, at)[0]
        code, size, at = head >> 6, head & 0x3F, at + 2
        if size == 0x3F:
            size, at = struct.unpack_from('<I', data, at)[0], at + 4
        if code == TAG_PLACE:
            out.append(_place(data[at:at + size]))
        at += size
    return ('sprite', out)


#: Each drawn tag's reader.
READERS = {TAG_BITS: _bitmap, TAG_SHAPE: _shape, TAG_TEXT: _text, TAG_SPRITE: _sprite}


def characters(tags: list) -> dict:
    """Every drawn character by id: `('image'|'shape'|'sprite'|'text', body)`."""
    return {struct.unpack_from('<H', tag.data)[0]: READERS[tag.code](tag.data)
            for tag in tags if tag.code in READERS}


def wrap(font, text: str, width: float) -> list:
    """`text` word-wrapped to `width` in `font`."""
    out = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split(' '):
            trial = f'{line} {word}'.strip()
            if line and font.getlength(trial) > width:
                out.append(line)
                line = word
            else:
                line = trial
        out.append(line)
    return out


def text_height(font, text: str, width: float) -> float:
    """What a field's `textHeight` reads for `text` wrapped to `width`."""
    return len(wrap(font, text, width - 2 * dlg.TEXT_GUTTER)) * sum(font.getmetrics()) \
        + 2 * dlg.TEXT_GUTTER


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

class Renderer:
    """Draws one movie's root display list onto a stage-sized image."""

    def __init__(self, movie, state: dict):
        """Reads `movie`'s characters and root placements; `state` overrides them."""
        self.chars = characters(movie.tags)
        self.roots = sorted((_place(t.data) for t in movie.tags if t.code == TAG_PLACE),
                            key=lambda p: p['depth'])
        self.state = state
        self.font = ImageFont.truetype(dlg.MW_FONT_PATH, dlg.FONT_PX)

    def render(self):
        """The stage, every visible root instance drawn in depth order."""
        canvas = Image.new('RGBA', (dlg.STAGE_W, dlg.STAGE_H), BACKDROP)
        for place in self.roots:
            self._instance(canvas, place)
        return canvas

    def _instance(self, canvas, place: dict) -> None:
        """One placed instance, as its state leaves it."""
        state = self.state.get(place['name'], {})
        if not state.get('visible', True):
            return
        kind, body = self.chars.get(place['char'], (None, None))
        if kind == 'text':
            self._text(canvas, body, state)
            return
        sx, sy, x, y = place['matrix']
        x, y = state.get('x', x), state.get('y', y)
        if 'width' in state or 'height' in state:
            w, h = self._size(place['char'])
            sx = state.get('width', w) / max(w, 1e-6)
            sy = state.get('height', h) / max(h, 1e-6)
        self._draw(canvas, place['char'], x, y, sx, sy)

    def _size(self, char: int) -> tuple:
        """A shape or sprite's natural size: its first fill's."""
        kind, body = self.chars[char]
        if kind == 'sprite':
            return self._size(body[0]['char'])
        fill = body[0]
        if fill[0] == 'solid':
            return fill[2][2], fill[2][3]
        image = self.chars[fill[1]][1]
        return image.width * fill[4], image.height * fill[5]

    def _draw(self, canvas, char: int, x: float, y: float, sx: float, sy: float) -> None:
        """A shape's fills, or a sprite's children (each as the state names
        it), at `x`, `y` scaled `sx`, `sy`."""
        kind, body = self.chars[char]
        if kind == 'sprite':
            for child in body:
                state = self.state.get(child['name'], {})
                if not state.get('visible', True):
                    continue
                csx, csy, cx, cy = child['matrix']
                if self.chars[child['char']][0] == 'text':
                    self._text(canvas, self.chars[child['char']][1], state, (x + cx, y + cy))
                    continue
                self._draw(canvas, child['char'], x + cx * sx, y + cy * sy, sx * csx, sy * csy)
            return
        for fill in body:
            self._fill(canvas, fill, x, y, sx, sy)

    def _fill(self, canvas, fill: tuple, x: float, y: float, sx: float, sy: float) -> None:
        """One shape fill, scaled and placed."""
        if fill[0] == 'solid':
            fx, fy, w, h = fill[2]
            piece = Image.new('RGBA', (max(1, round(w * sx)), max(1, round(h * sy))), fill[1])
            canvas.alpha_composite(piece, (round(x + fx * sx), round(y + fy * sy)))
            return
        image = self.chars[fill[1]][1]
        size = (max(1, round(image.width * fill[4] * sx)),
                max(1, round(image.height * fill[5] * sy)))
        canvas.alpha_composite(image.resize(size, Image.LANCZOS),
                               (round(x + fill[2] * sx), round(y + fill[3] * sy)))

    def _text(self, canvas, field: dict, state: dict, origin: tuple = (0, 0)) -> None:
        """A field's text, wrapped and aligned inside its rect, from `origin`."""
        text = state.get('text', field['text'])
        if not text:
            return
        x, y, w, _h = field['rect']
        x, y = state.get('x', x + origin[0]), state.get('y', y + origin[1])
        draw = ImageDraw.Draw(canvas)
        rgb = state.get('color', field['rgb'])
        pitch = sum(self.font.getmetrics())
        inner = w - 2 * dlg.TEXT_GUTTER
        for i, line in enumerate(wrap(self.font, text, inner)):
            room = inner - self.font.getlength(line)
            dx = (0, room, room / 2)[field['align']]
            draw.text((x + dlg.TEXT_GUTTER + dx, y + dlg.TEXT_GUTTER + i * pitch),
                      line, font=self.font, fill=(*rgb, 255))


def render(movie, state: dict):
    """`movie` drawn with `state` applied."""
    return Renderer(movie, state).render()


# ---------------------------------------------------------------------------
# Sample states
# ---------------------------------------------------------------------------

def hidden(*names) -> dict:
    """`visible: False` for each name."""
    return {name: {'visible': False} for name in names}


def caption(origin: tuple, rect: tuple, title: str, font) -> dict:
    """The cover and caps parted round the title as the plugin parts them."""
    x, _y, w, _h = rect
    gap = font.getlength(title) + 2 * dlg.CAPTION_PAD
    left = origin[0] + x + (w - gap) / 2
    return {'Cover': {'x': left, 'width': gap}, 'CapLeft': {'x': left - 2},
            'CapRight': {'x': left + gap}}


def tip_hidden() -> dict:
    """Every tooltip piece hidden, as the plugin leaves them when nothing is hovered."""
    names = ['TipTop', 'TipMid', 'TipBottom', 'TipName', 'TipAttr', 'TipText', 'TipLabel',
             'TipProgress', 'TipBar', 'TipBarCover']
    names += [f'TipIcon{i}' for i in range(st.ATTRIBUTES)]
    names += [f'TipSkill{av}' for av in st.SKYRIM_SKILLS]
    return hidden(*names)


def tip_shown(tip: dict, at: tuple, font) -> dict:
    """One tooltip laid out as `StatTip::Place` lays it.

    `tip`: `icon`, `name`, `attr` ('' for an attribute), `text`, `progress` (0..100 or None).
    """
    text_h = text_height(font, tip['text'], st.TIP_W - 2 * st.TIP_PAD)
    extra = 0 if tip['progress'] is None else st.TIP_GAP + st.TIP_LABEL_H + st.TIP_BAR_H
    left, top = at[0] - at[0] / dlg.STAGE_W * st.TIP_W, at[1] + 32
    name_dy = dlg.text_top(st.TIP_PAD, st.TIP_LINE if tip['attr'] else st.TIP_ICON)
    state = tip_hidden()
    state.update({
        'TipTop': {'x': left, 'y': top},
        'TipMid': {'x': left, 'y': top + st.TIP_TOP, 'height': text_h + extra},
        'TipBottom': {'x': left, 'y': top + st.TIP_TOP + text_h + extra},
        tip['icon']: {'x': left + st.TIP_PAD, 'y': top + st.TIP_PAD},
        'TipName': {'x': left + st.tip_name_x(), 'y': top + name_dy, 'text': tip['name']},
        'TipText': {'x': left + st.TIP_PAD, 'y': top + st.TIP_TOP, 'text': tip['text']}})
    if tip['attr']:
        state['TipAttr'] = {'x': left + st.tip_name_x(), 'text': tip['attr'],
                            'y': top + dlg.text_top(st.TIP_PAD + st.TIP_LINE, st.TIP_LINE)}
    if tip['progress'] is not None:
        state.update(tip_progress(left, top + st.TIP_TOP + text_h, tip['progress']))
    return state


def tip_progress(left: float, top: float, progress: int) -> dict:
    """The label, the bar, its cover and its number, under the description."""
    label_y = top + st.TIP_GAP
    bar_x, bar_y = left + (st.TIP_W - st.TIP_BAR_W) / 2, label_y + st.TIP_LABEL_H
    fill = st.TIP_BAR_W - 4
    filled = fill * progress / 100
    return {'TipLabel': {'x': left + st.TIP_PAD, 'y': label_y + dlg.text_top(0, st.TIP_LABEL_H),
                         'text': 'Progress towards skill increase'},
            'TipBar': {'x': bar_x, 'y': bar_y},
            'TipBarCover': {'x': bar_x + 2 + filled, 'y': bar_y + 2, 'width': max(1, fill - filled)},
            'TipProgress': {'x': bar_x, 'y': bar_y + dlg.text_top(0, st.TIP_BAR_H),
                            'text': f'{progress}/100'}}


#: The stats window's sample character.
ATTRIBUTES = (('Strength', 45), ('Intelligence', 52), ('Willpower', 38), ('Agility', 41),
              ('Speed', 50), ('Endurance', 44), ('Personality', 30), ('Luck', 40))
#: Skyrim's skills in the runtime's three groups (stats_sheet.cpp `kSkillGroups`), as actor values.
SKILL_GROUPS = (('Combat', (6, 7, 8, 9, 10, 11)), ('Magic', (18, 19, 20, 21, 22, 23)),
                ('Stealth', (12, 13, 14, 15, 16, 17)))

#: The runtime's English names, for a machine whose Skyrim strings cannot be read.
ENGLISH_SKILLS = ('One-handed', 'Two-handed', 'Archery', 'Block', 'Smithing', 'Heavy Armor',
                  'Light Armor', 'Pickpocket', 'Lockpicking', 'Sneak', 'Alchemy', 'Speech',
                  'Alteration', 'Conjuration', 'Destruction', 'Illusion', 'Restoration',
                  'Enchanting')


def skyrim_skills() -> dict:
    """`{actor value: (name, description)}`: the packaged table's, else English and none."""
    out = {6 + i: (name, '') for i, name in enumerate(ENGLISH_SKILLS)}
    for line in skill_table_text().splitlines():
        av, _, rest = line.partition('=')
        name, _, text = rest.partition('|')
        out[int(av)] = (name, text.replace(chr(92) + 'n', chr(10)))
    return out


def skill_rows(skills: dict) -> list:
    """The skill list as the runtime builds it: each group's heading, then its
    skills by name with a sample value (None marks a heading or a blank row)."""
    rows = []
    for heading, group in SKILL_GROUPS:
        if rows:
            rows.append(('', None))
        rows.append((heading, None))
        rows += sorted((skills[av][0], 10 + 3 * (av % 7)) for av in group)
    return rows

#: The three bars' sample values: label, now, most.
BARS = (('Health', 85, 100), ('Magicka', 40, 90), ('Fatigue', 120, 120))


def stats_state(art, tip: dict, skills: dict) -> dict:
    """The stats window with a sample character and `skills`' names, and `tip` over it."""
    font = ImageFont.truetype(dlg.MW_FONT_PATH, dlg.FONT_PX)
    origin = st.stats_origin()
    state = caption(origin, st.CAPTION, 'Nerevar', font)
    state['Title'] = {'text': 'Nerevar'}
    for row, (label, now, most) in enumerate(BARS):
        x, _y, w, _h = st.on_stage(st.bar_fill_rect(row), origin)
        filled = w * now / most
        state[f'BarLabel{row}'] = {'text': label}
        state[f'BarValue{row}'] = {'text': f'{now}/{most}'}
        state[f'BarCover{row}'] = {'x': x + filled, 'width': max(1, w - filled),
                                   'visible': filled < w}
    for row, (name, value) in enumerate((('Level', 3), ('Race', 'Dark Elf'), ('Class', 'Battlemage'))):
        state[f'InfoName{row}'], state[f'InfoValue{row}'] = {'text': name}, {'text': str(value)}
    for row, (name, value) in enumerate(ATTRIBUTES):
        state[f'AttrName{row}'], state[f'AttrValue{row}'] = {'text': name}, {'text': str(value)}
    tab = 1 if tip is STATISTICS else 0
    for i, label in enumerate(('Skills', 'Statistics')):
        state[f'Tab{i}'] = {'text': label,
                            'color': art.colors['normal_pressed' if i == tab else 'normal']}
    rows = STATISTIC_ROWS if tab else skill_rows(skills)
    for row, (name, value) in enumerate(rows[:st.skill_view()[3] // st.ROW_H]):
        state[f'SkillName{row}'] = {'text': name}
        state[f'SkillValue{row}'] = {'text': '' if value is None else str(value)}
        if value is None:
            state[f'SkillName{row}']['color'] = art.colors['header']
    state.update(tip_hidden() if tab else tip)
    return state


#: The Statistics tab's sample rows (None marks a heading), and the marker that asks for them.
STATISTIC_ROWS = (('Birth Sign', 'The Lady'), ('', None),
                  ('Oblivion', None), ('Bounty', 40), ('Shivering Isles Bounty', 0),
                  ('Fame', 3), ('Oblivion Gates Shut', 4), ('', None),
                  ('Nehrim', None), ('Total XP', 4120), ('Learning Points', 7),
                  ('Magical symbols found', 2), ('Bank balance', 1250), ('', None),
                  ('Morroblivion', None), ('Reputation', 2), ('Bounty', 0))
STATISTICS = {}


#: The level-up sample: each attribute's multiplier, and the two already chosen.
GAINS = {0: 3, 1: 2, 5: 2, 7: 1}
CHOSEN = (0, 5)


def levelup_state(font) -> dict:
    """The level-up dialog mid-step: two coins spent, multipliers shown."""
    origin = st.dialog_origin()
    state = {f'Class_{name}': {'visible': name == 'warrior'} for name in st.CLASSES}
    state['LevelText'] = {'text': 'You have ascended to Level 3.'}
    state['Description'] = {'text': 'Through discipline and training your skills have grown. '
                                    'Choose three attributes to raise.'}
    state['OkCaption'] = {'text': 'OK'}
    for attribute, (name, value) in enumerate(ATTRIBUTES):
        x, _y = st.grid_row(attribute)
        gain = GAINS.get(attribute, 1)
        state[f'Mult{attribute}'] = {'text': f'x{gain}' if gain > 1 else ''}
        state[f'AttrName{attribute}'] = {'text': name}
        state[f'AttrValue{attribute}'] = {
            'text': str(value + (gain if attribute in CHOSEN else 0)),
            'x': origin[0] + x + st.MULTIPLIER_W + font.getlength(name) + 4}
    for coin, attribute in enumerate(CHOSEN):
        x, y = st.grid_row(attribute)
        shift = st.MULTIPLIER_W if GAINS[attribute] > 1 else 0
        state[f'Coin{coin}'] = {'x': origin[0] + x + st.MULTIPLIER_W - 22 - shift,
                                'y': origin[1] + y + (st.GRID_ROW_H - st.COIN) / 2}
    state['Coin2'] = {'x': origin[0] + st.COIN_ROW[2] / 2 - st.COIN / 2,
                      'y': origin[1] + st.COIN_ROW[1]}
    state.update(tip_hidden())
    return state


#: The dialogue sample's history and topics.
HISTORY = ('Background\n\nI was born in the Imperial City. Like most of my kind, I joined the '
           'Legion as a young man, and served the Emperor in many lands.\n\nduties\n\nYou are a '
           'Blade now, a Novice of the order.')
TOPICS = ('Barter', 'Persuasion', '', 'Blades', 'background', 'duties', 'latest rumors',
          'little advice', 'little secret', 'my trade', 'Nerevarine', 'orders',
          'specific place')


def dialogue_state(font) -> dict:
    """The dialogue window mid-conversation, the persuasion modal hidden."""
    state = caption(dlg.window_origin(), dlg.CAPTION, 'Caius Cosades', font)
    state.update({'Name': {'text': 'Caius Cosades'}, 'History': {'text': HISTORY},
                  'Disposition': {'text': '62'}, 'Bye': {'text': 'Goodbye'}})
    for row, topic in enumerate(TOPICS):
        state[f'Topic{row}'] = {'text': topic}
    state.update(hidden('Persuade', 'PersuadeTitle', 'PersuadeGold', 'PersuadeCancel',
                        *[f'PersuadeRow{i}' for i in range(dlg.MODAL_ROWS)]))
    return state


#: The class menu sample: a few classes, the chosen one, and a custom class's picks.
CLASS_ROWS = ('Acrobat', 'Agent', 'Archer', 'Assassin', 'Barbarian', 'Bard', 'Battlemage')
SIGN_ROWS = ('The Apprentice', 'The Atronach', 'The Lady', 'The Lord', 'The Lover',
             'The Mage', 'The Ritual')


def list_state(art, rows: tuple, chosen: int) -> dict:
    """A list's rows with `chosen` lit, as the plugin colors them."""
    state = {f'Row{i}': {'text': name} for i, name in enumerate(rows)}
    state[f'Row{chosen}']['color'] = art.colors['normal_pressed']
    return state


def class_state(art, custom: bool) -> dict:
    """The class menu on Battlemage, or on a custom class with two picks made."""
    state = list_state(art, CLASS_ROWS, 6)
    state.update({f'Class_{name}': {'visible': name == 'battlemage'} for name in st.CLASSES})
    state.update({'SpecHeader': {'text': 'Specialization:'}, 'SpecName': {'text': 'Magic'},
                  'FavHeader': {'text': 'Favorite Attributes:'},
                  'Fav0': {'text': 'Strength'}, 'Fav1': {'text': 'Intelligence'},
                  'OkCaption': {'text': 'OK'}})
    names = [name for name, _value in ATTRIBUTES]
    if custom:
        state.update({f'Pick{a}': {'text': name} for a, name in enumerate(names)})
        state.update({'Pick0': {'text': 'Strength', 'color': art.colors['normal_pressed']},
                      'Pick1': {'text': 'Intelligence', 'color': art.colors['normal_pressed']},
                      'PickHeader': {'text': 'Favorite Attributes:'},
                      'NameLabel': {'text': 'Name'}, 'NameText': {'text': 'SpellBlade_'},
                      'SpecName': {'text': 'Combat'}})
        state.update(hidden('Description'))
    else:
        state['Description'] = {'text': 'Wizard-warriors trained in both lethal spellcasting '
                                        'and heavily armored combat.'}
        state.update(hidden('PickHeader', 'NameLabel', 'NameText', 'NameBox',
                            *[f'Pick{a}' for a in range(len(names))]))
    return state


#: The Lady's lines as the shared table writes them for Morrowind, and the icon they show.
LADY_LINES = (('h', '', 'Abilities:'), ('s', '', "Lady's Favor"),
              ('e', 'icons_s_tx_s_ftfy_attrib', 'Fortify Personality 25 pts'),
              ('s', '', "Lady's Grace"),
              ('e', 'icons_s_tx_s_ftfy_attrib', 'Fortify Endurance 25 pts'))
LADY_ICONS = {'icons_s_tx_s_ftfy_attrib': 'icons\\s\\Tx_S_Ftfy_Attrib.dds'}


def birth_state(art) -> dict:
    """The birthsign menu on The Lady, its lines placed as the plugin places them."""
    state = list_state(art, SIGN_ROWS, 2)
    birth = cg.origin(cg.BIRTH_W, cg.BIRTH_H)
    state['Sign_lady'] = {'x': birth[0] + cg.BIRTH_IMAGE[0]}
    left = birth[0] + cg.birth_line(0)[0]
    for row, (kind, key, text) in enumerate(LADY_LINES):
        color = art.colors['header'] if kind == 'h' else art.colors['normal']
        shift = cg.ICON_INDENT if kind == 'e' else 0
        state[f'Line{row}'] = {'text': text, 'color': color, 'x': left + shift}
        if key:
            state[f'Icon{row}_{key}'] = {'x': left + cg.ICON_LEFT}
    state['OkCaption'] = {'text': 'OK'}
    return state


def button_state(hover: bool, back: bool) -> dict:
    """The perks button in a 16:9 screen's corner, as perks_button.cpp places it."""
    y = dlg.STAGE_H - (st.PERKS_BAR_H + st.BUTTON_GLOW[3]) // 2
    return {'Button': {'x': st.BUTTON_MARGIN, 'y': y}, 'Glow': {'visible': hover},
            'Key': {'text': 'C'},
            'Label': {'text': 'BACK TO PERKS' if back else 'CHARACTER',
                      'color': (255, 255, 255) if hover else st.BUTTON_GRAY}}


def scenes(art, export_root: str) -> list:
    """`(file name, movie, state)` for every sample this writes; the birthsign
    menu's effect icon comes from whichever install holds it."""
    font = ImageFont.truetype(dlg.MW_FONT_PATH, dlg.FONT_PX)
    stats = st.stats_window(art)
    attr_x, attr_y, _w, _h = st.on_stage(st.row_rect(st.ATTRIBUTE_BOX, st.BOX_PAD),
                                         st.stats_origin())
    skill_x, skill_y, _w, _h = st.on_stage(st.skill_view(), st.stats_origin())
    attribute_tip = tip_shown({'icon': 'TipIcon1', 'name': 'Intelligence', 'attr': '',
                               'text': 'Affects your maximum amount of Magicka.',
                               'progress': None}, (attr_x + 40, attr_y + st.ROW_H + 9), font)
    skills = skyrim_skills()
    skill_tip = tip_shown({'icon': 'TipSkill6', 'name': skills[6][0],
                           'attr': 'Governing Attribute: Strength', 'text': skills[6][1],
                           'progress': 64}, (skill_x + 40, skill_y + 5 * st.ROW_H + 9), font)
    return [('dialogue.png', dlg.dialogue_window(art), dialogue_state(font)),
            ('stats.png', stats, stats_state(art, tip_hidden(), skills)),
            ('stats_attribute_tip.png', stats, stats_state(art, attribute_tip, skills)),
            ('stats_skill_tip.png', stats, stats_state(art, skill_tip, skills)),
            ('stats_statistics.png', stats, stats_state(art, STATISTICS, skills)),
            ('levelup.png', st.levelup_dialog(art), levelup_state(font)),
            ('class.png', cg.class_window(art), class_state(art, False)),
            ('class_custom.png', cg.class_window(art), class_state(art, True)),
            ('birthsign.png', cg.birth_window(art, effect_icons(export_root, LADY_ICONS)),
             birth_state(art)),
            ('perks_button.png', st.perks_button(art), button_state(False, False)),
            ('perks_button_back_hover.png', st.perks_button(art), button_state(True, True))]


def main() -> int:
    """CLI: render every scene in the chosen style into `--out`."""
    ap = argparse.ArgumentParser(description='Render the runtime menus to PNGs.')
    ap.add_argument('--out', required=True)
    ap.add_argument('--export-root', default='export')
    ap.add_argument('--style', choices=STYLES, default=None)
    ap.add_argument('--icons', choices=ICON_SETS, default=None)
    args = ap.parse_args()
    art = menu_art(args.export_root, args.style, args.icons)
    os.makedirs(args.out, exist_ok=True)
    for name, movie, state in scenes(art, args.export_root):
        path = os.path.join(args.out, name)
        render(movie, state).convert('RGB').save(path)
        print(f'wrote {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

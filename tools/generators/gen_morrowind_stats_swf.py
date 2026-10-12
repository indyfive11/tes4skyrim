"""
Author the Morrowind stats window and level-up dialog SWFs, and their layout header.

Two standalone movies, each a menu of its own, drawn exactly like the dialogue
window (`gen_morrowind_menu_swf.py`): the art object's chrome composed into one
bitmap, OpenMW's embedded face, and every string a dynamic field the plugin
writes. Rows are single-line fields the plugin places, so a list's pitch is the
layout's, not the font's.

- `morrowind_stats.swf`: `openmw_stats_window.layout`.
- `morrowind_levelup.swf`: `openmw_levelup_dialog.layout`, with every class
  image as a hidden sprite and the gold coins.

🛑 Morrowind's art is NOT committed; the Skyrim style needs none. The header
IS committed, so the plugin builds without either.

See: docs/plans/character_sheet.md#m2-swf
See: docs/commentary/morrowind_runtime.md#menu-styles
"""

import argparse
import os
import struct

from PIL import Image, ImageDraw

from asset_convert.ui.menu_art import ICON_SETS, STYLES, menu_art
from asset_convert.ui.morrowind_menu_art import SCROLL_TRACK, SCROLL_W
from asset_convert.ui.swf import (Swf, Tag, define_bits_lossless2,
                                  define_shape3_bitmap_rects,
                                  define_shape3_solid_rects, define_sprite,
                                  pack_rect, place_object2)
from asset_convert.ui.ui_menus import premultiplied_argb
from tools.generators.gen_morrowind_menu_swf import (
    ALIGN_CENTER, ALIGN_LEFT, ALIGN_RIGHT, BODY_HEIGHT_TWIPS, CAPTION_PAD,
    CHAR_FONT, CHAR_MW_FONT, STAGE_H, STAGE_W, TAG_END, TAG_FILE_ATTRIBUTES,
    TAG_SET_BACKGROUND_COLOR, TAG_SHOW_FRAME, THUMB_H, THUMB_W, THUMB_X, TWIP,
    define_edit_text, embed_font, import_font, style_marker, text_top)

#: Where the plugin's copy of both layouts is written.
HEADER_PATH = 'tes_runtime/morrowind/plugin/stats_layout.h'

#: The movies, as the game loads them under Interface/.
STATS_MOVIE = 'morrowind_stats.swf'
LEVELUP_MOVIE = 'morrowind_levelup.swf'
BUTTON_MOVIE = 'morrowind_perks_button.swf'

#: `Morrowind.ini` `[FontColor]` color_health, color_magic, color_fatigue: the three bar tints.
BAR_COLORS = ((200, 60, 30), (53, 69, 159), (0, 150, 60))

#: MW_Window at `openmw_stats_window.layout`'s size, its caption strip, client origin and inner frame.
STATS_W = 500
STATS_H = 342
CAPTION = (4, 4, STATS_W - 8, 20)
CLIENT = (8, 28)
INNER_FRAME = (4, 24, STATS_W - 8, STATS_H - 28)

#: The layout's MW_Box panes in CLIENT space: dynamic stats, level box, attributes, skills.
DYNAMIC_BOX = (8, 8, 212, 62)
INFO_BOX = (8, 78, 212, 62)
ATTRIBUTE_BOX = (8, 148, 212, 152)
SKILL_BOX = (228, 34, 248, 266)

#: The Skills and Statistics tabs above the right pane, in CLIENT space.
TABS = ((228, 8, 122, 22), (354, 8, 122, 22))

#: A dynamic stat row inside its box: 4 px in, 18 px apart; the label 70 wide, the bar from 74 to 204.
BOX_PAD = 4
ROW_H = 18
BAR_LABEL_W = 70
BAR_X = 74
BAR_W = 130

#: The level box's HBoxes sit at these offsets inside it.
INFO_ROWS = (4, 24, 42)

#: Attribute rows, each ROW_H tall.
ATTRIBUTE_ROWS = 8

#: Row fields kept for the skill list; the plugin places each, and a partly shown row is hidden.
SKILL_FIELDS = 16

#: Character ids for the stats movie: chrome, the caption cover, three bar covers, sprites, fields.
CHAR_STATS_BMP = 20
CHAR_COVER = 22
CHAR_BAR_COVER_FIRST = 30
CHAR_STATS_SPRITE_FIRST = 60
CHAR_STATS_FIELD_FIRST = 100

#: MW_Dialog's size: 10 px padding around a 420 px column, children 8 px apart.
DIALOG_W = 440
DIALOG_H = 496

#: The class image box and the image inside it, stretched from the texture's 256x128.
IMAGE_BOX = (24, 10, 391, 198)
IMAGE = (28, 14, 383, 190)

#: The level line, the level text, the coin row, the attribute grid and OK, in DIALOG space.
LEVEL_TEXT = (24, 216, 391, 24)
DESCRIPTION = (24, 248, 391, 90)
COIN_ROW = (0, 346, DIALOG_W, 16)
ASSIGN = (10, 370, 420, 84)
OK_BUTTON = (DIALOG_W - 10 - 64, 462, 64, 24)

#: LevelupDialog's grid: two columns at these x offsets, 20 px rows, the multiplier 20 px before the name.
COLUMNS = (32, 218)
GRID_ROW_H = 20
MULTIPLIER_W = 20
#: OpenMW's multiplier box is 100 px and runs under the name: "x2" is wider than 20 px.
MULTIPLIER_FIELD_W = 100
NAME_W = 120
VALUE_W = 40

#: Attributes per grid column: OpenMW's ceil(attributes / columns).
PER_COLUMN = (ATTRIBUTE_ROWS + len(COLUMNS) - 1) // len(COLUMNS)

#: The coin icon's size, the gap resetCoins leaves between coins, and how many there are.
COIN = 16
COIN_SPACING = 33
COINS = 3

#: MW_Button's caption inset, as the dialogue window's Goodbye.
BUTTON_INSET = (4, 3)

#: Every class image LevelupDialog::getLevelupClassImage can name.
CLASSES = ('acrobat', 'agent', 'archer', 'assassin', 'barbarian', 'bard',
           'battlemage', 'crusader', 'healer', 'knight', 'mage', 'monk',
           'nightblade', 'pilgrim', 'rogue', 'scout', 'sorcerer',
           'spellsword', 'thief', 'warrior', 'witchhunter')

#: The class texture's own pixel size, which the image rect stretches.
CLASS_PIXELS = (256, 128)

#: Character ids for the level-up movie: chrome, coins, class images (three each), then fields.
CHAR_DIALOG_BMP = 20
CHAR_COIN_FIRST = 30
CHAR_CLASS_FIRST = 60
CHAR_DIALOG_FIELD_FIRST = 200

#: OpenMW's AttributeToolTip in HUD_Box_NoTransp: our width, its 8 px padding and 32 px icon.
TIP_W = 320
TIP_PAD = 8
TIP_ICON = 32
#: The box's top piece (padding, icon row, spacing) and the description's tallest field.
TIP_TOP = TIP_PAD + TIP_ICON + TIP_PAD
TIP_TEXT_H = 200

#: SkillToolTip: name and governing attribute as two 16 px lines beside the icon.
TIP_LINE = 16
#: Then a 2 px gap, the 18 px progress label and the 200x20 MW_Progress_Red bar.
TIP_GAP = 2
TIP_LABEL_H = 18
TIP_BAR_W = 200
TIP_BAR_H = 20

#: The TES3 attribute count and Skyrim's skills (actor values 6..23), one tooltip icon each.
ATTRIBUTES = 8
SKYRIM_SKILLS = range(6, 24)

#: Tooltip character ids: box pieces, icons and bar (three each), the bar's cover, then fields.
CHAR_TIP_SPRITE_FIRST = 400
CHAR_TIP_COVER = 560
CHAR_TIP_FIELD_FIRST = 600

#: The perks menu's button in its own space: the hover glow (also the click area), the key cap, the label.
BUTTON_GLOW = (0, 0, 200, 36)
BUTTON_KEY = (8, 5, 26, 26)
BUTTON_LABEL = (44, 6, 156, 26)

#: Its text in the perks menu's own face: the key's letter, and the label in vanilla's bar gray.
BUTTON_KEY_PX = 17
BUTTON_LABEL_PX = 19
BUTTON_INK = (20, 20, 20)
BUTTON_GRAY = (153, 153, 153)

#: The key cap and glow are drawn this many times their stage size, so a 4K screen still shows them sharp.
BUTTON_SUPERSAMPLE = 4

#: Vanilla statsmenu.swf's bottom bar, which the button centers in, and its gap from the screen's left edge.
PERKS_BAR_H = 72
BUTTON_MARGIN = 18

#: Button character ids: key cap and glow (bitmap, shape), the glow's sprite, two fields, the button sprite.
CHAR_BUTTON_FIRST = 700


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------

def stats_origin() -> tuple:
    """The stats window's top-left on the stage: centered."""
    return ((STAGE_W - STATS_W) // 2, (STAGE_H - STATS_H) // 2)


def dialog_origin() -> tuple:
    """The level-up dialog's top-left on the stage: centered."""
    return ((STAGE_W - DIALOG_W) // 2, (STAGE_H - DIALOG_H) // 2)


def client(rect: tuple) -> tuple:
    """A CLIENT-space rect of the stats window moved into WINDOW space."""
    return (rect[0] + CLIENT[0], rect[1] + CLIENT[1], rect[2], rect[3])


def on_stage(rect: tuple, origin: tuple) -> tuple:
    """A window-space rect moved onto the stage."""
    return (rect[0] + origin[0], rect[1] + origin[1], rect[2], rect[3])


def line_in(box: tuple) -> tuple:
    """A field rect whose one line is vertically centered in `box`."""
    x, y, w, h = box
    return (x, text_top(y, h), w, h)


def field(char_id: int, rect: tuple, origin: tuple, colors: dict,
          color: str = 'normal', align: int = ALIGN_LEFT, html: bool = False) -> Tag:
    """A dynamic field in the embedded face at a window-space rect."""
    return define_edit_text(char_id, *on_stage(rect, origin), '', '',
                            font_id=CHAR_MW_FONT, rgb=colors[color],
                            height=BODY_HEIGHT_TWIPS, align=align, html=html)


def sprite(char_id: int, image, name: str, rect: tuple, origin: tuple,
           pixels: tuple = None) -> list:
    """`image` in a one-frame SPRITE placed at a window-space rect.

    Returns `[bitmap, shape, sprite, (sprite_id, name, stage_xy)]`. With
    `pixels`, the bitmap's own size, the rect's size stretches it.
    """
    x, y, w, h = on_stage(rect, origin)
    iw, ih = image.size
    piece = (char_id, 0, 0, w, h, pixels) if pixels else (char_id, 0, 0, iw, ih)
    return [
        define_bits_lossless2(char_id, iw, ih, premultiplied_argb(image)),
        define_shape3_bitmap_rects(char_id + 1, [piece]),
        define_sprite(char_id + 2,
                      [place_object2(depth=1, character_id=char_id + 1)]),
        (char_id + 2, name, (x, y)),
    ]


def cover(char_id: int, name: str, rect: tuple, origin: tuple,
          depth: int, rgba: tuple = (0, 0, 0, 255)) -> list:
    """A 1 px wide `rgba` sprite `rect[3]` tall at a window-space rect's
    top-left; the plugin sets its `_x` and `_width`."""
    x, y, _w, h = on_stage(rect, origin)
    return [
        define_shape3_solid_rects(char_id, [(0, 0, 1, h)], rgba),
        define_sprite(char_id + 1, [place_object2(depth=1, character_id=char_id)]),
        place_object2(depth=depth, character_id=char_id + 1, name=name,
                      translate=(x, y)),
    ]


def chrome_tags(char_bmp: int, image, origin: tuple) -> list:
    """The composed chrome as one bitmap shape at depth 1."""
    w, h = image.size
    return [
        define_bits_lossless2(char_bmp, w, h, premultiplied_argb(image)),
        define_shape3_bitmap_rects(char_bmp + 1,
                                   [(char_bmp, origin[0], origin[1], w, h)]),
        place_object2(depth=1, character_id=char_bmp + 1, name='Window_mc'),
    ]


def place_all(parts: list, fields: list, depth: int) -> list:
    """Sprites, then fields, each at the next depth from `depth`."""
    tags = []
    for bitmap, shape, clip, (char_id, name, at) in parts:
        tags += [bitmap, shape, clip,
                 place_object2(depth=depth, character_id=char_id, name=name,
                               translate=at)]
        depth += 1
    for tag, name in fields:
        tags += [tag, place_object2(depth=depth,
                                    character_id=tag.character_id, name=name)]
        depth += 1
    return tags


def tip_sprites(art) -> list:
    """The tooltip box as top, a 1 px middle the plugin stretches, and bottom;
    each attribute and skill icon a game supplies; the skill progress bar.

    All at the stage origin: the plugin moves every piece beside the pointer.
    See: docs/commentary/morrowind_runtime.md#attribute-tooltips
    """
    box = art.compose_box(TIP_W, TIP_TOP + 1 + TIP_PAD, fill=art.background)
    pieces = [(box.crop((0, 0, TIP_W, TIP_TOP)), 'TipTop'),
              (box.crop((0, TIP_TOP, TIP_W, TIP_TOP + 1)), 'TipMid'),
              (box.crop((0, TIP_TOP + 1, TIP_W, TIP_TOP + 1 + TIP_PAD)), 'TipBottom')]
    icons = [(art.icons.attribute(i), f'TipIcon{i}') for i in range(ATTRIBUTES)]
    icons += [(art.icons.skill(av), f'TipSkill{av}') for av in SKYRIM_SKILLS]
    pieces += [(image.resize((TIP_ICON, TIP_ICON)), name) for image, name in icons if image]
    pieces.append((art.compose_stat_bar(TIP_BAR_W, TIP_BAR_H, BAR_COLORS[0]), 'TipBar'))
    return [sprite(CHAR_TIP_SPRITE_FIRST + 3 * i, image, name, (0, 0, *image.size), (0, 0))
            for i, (image, name) in enumerate(pieces)]


def tip_name_x() -> int:
    """Where the name starts, past the icon, inside the box."""
    return TIP_PAD + TIP_ICON + TIP_PAD


def tip_fields(colors: dict) -> list:
    """The tooltip's name and governing attribute beside the icon, its wrapped
    text (HTML, for the faction tooltip's two colors), and the progress label
    and value, all at the origin."""
    name_w, inner_w = TIP_W - tip_name_x() - TIP_PAD, TIP_W - 2 * TIP_PAD
    rows = (('TipName', name_w, TIP_ICON, ALIGN_LEFT),
            ('TipAttr', name_w, TIP_LINE, ALIGN_LEFT),
            ('TipText', inner_w, TIP_TEXT_H, ALIGN_LEFT),
            ('TipLabel', inner_w, TIP_LABEL_H, ALIGN_CENTER),
            ('TipProgress', TIP_BAR_W, TIP_BAR_H, ALIGN_CENTER))
    return [(field(CHAR_TIP_FIELD_FIRST + i, (0, 0, w, h), (0, 0), colors, align=align,
                   html=name == 'TipText'), name)
            for i, (name, w, h, align) in enumerate(rows)]


def tip_tags(art, depth: int) -> list:
    """The tooltip above everything else, from `depth` up: its sprites, the
    cover the plugin slides over the bar past the progress, then its text."""
    sprites = tip_sprites(art)
    tags = place_all(sprites, [], depth)
    depth += len(sprites)
    tags += cover(CHAR_TIP_COVER, 'TipBarCover', (0, 0, 1, TIP_BAR_H - 4), (0, 0), depth)
    return tags + place_all([], tip_fields(art.colors), depth + 1)


def movie(tags: list, font: Tag = None) -> Swf:
    """A one-frame stage-sized movie of `tags`, with `font` (the embedded face when None)."""
    head = [Tag(TAG_FILE_ATTRIBUTES, struct.pack('<I', 0)),
            Tag(TAG_SET_BACKGROUND_COLOR, bytes([0, 0, 0])), font or embed_font()]
    tail = [Tag(TAG_SHOW_FRAME, b''), Tag(TAG_END, b'')]
    return Swf(version=9,
               frame_size=pack_rect(0, STAGE_W * TWIP, 0, STAGE_H * TWIP),
               framerate=(24 << 8), framecount=1, tags=head + tags + tail)


# ---------------------------------------------------------------------------
# Stats window
# ---------------------------------------------------------------------------

def bar_rect(row: int) -> tuple:
    """Dynamic stat `row`'s bar, in WINDOW space."""
    x, y, _w, _h = client(DYNAMIC_BOX)
    return (x + BOX_PAD + BAR_X, y + BOX_PAD + row * ROW_H, BAR_W, ROW_H)


def bar_fill_rect(row: int) -> tuple:
    """The tinted part of a bar, inside its box border, in WINDOW space."""
    x, y, w, h = bar_rect(row)
    return (x + 2, y + 2, w - 4, h - 4)


def row_rect(box: tuple, offset: int) -> tuple:
    """A full-width row `offset` px down a CLIENT-space box, in WINDOW space."""
    x, y, w, _h = client(box)
    return (x + BOX_PAD, y + offset, w - 2 * BOX_PAD, ROW_H)


def skill_view() -> tuple:
    """The skill list's rows area, beside its scrollbar, in WINDOW space."""
    x, y, w, h = client(SKILL_BOX)
    return (x + BOX_PAD, y + BOX_PAD, w - 2 * BOX_PAD - SCROLL_W - 2,
            h - 2 * BOX_PAD)


def skill_scroll() -> tuple:
    """The skill list's scrollbar, at the box's inner right edge, in WINDOW space."""
    x, y, w, h = client(SKILL_BOX)
    return (x + w - BOX_PAD - SCROLL_W, y + BOX_PAD, SCROLL_W, h - 2 * BOX_PAD)


def compose_stats(art):
    """The stats window's chrome: frames, caption plate, four panes, three full bars."""
    panel = art.compose_frame(STATS_W, STATS_H, fill=art.background)
    ix, iy, iw, ih = INNER_FRAME
    panel.alpha_composite(art.compose_frame(iw, ih), (ix, iy))
    cx, cy, cw, ch = CAPTION
    panel.alpha_composite(art.compose_head(cw, ch), (cx, cy))
    for box in (DYNAMIC_BOX, INFO_BOX, ATTRIBUTE_BOX, SKILL_BOX):
        bx, by, bw, bh = client(box)
        panel.alpha_composite(art.compose_box(bw, bh), (bx, by))
    for tab in TABS:
        tx, ty, tw, th = client(tab)
        panel.alpha_composite(art.compose_button(tw, th), (tx, ty))
    for row, rgb in enumerate(BAR_COLORS):
        bx, by, bw, bh = bar_rect(row)
        panel.alpha_composite(art.compose_stat_bar(bw, bh, rgb),
                              (bx, by))
    return panel


def paired_rows(ids, rects: list, name: str, colors: dict) -> list:
    """A name field and a right-aligned value field per rect: `Name<i>`, `Value<i>`."""
    origin, out = stats_origin(), []
    for row, rect in enumerate(rects):
        out.append((field(next(ids), line_in(rect), origin, colors), f'{name}Name{row}'))
        out.append((field(next(ids), line_in(rect), origin, colors, 'header',
                          ALIGN_RIGHT), f'{name}Value{row}'))
    return out


def stats_fields(colors: dict) -> list:
    """Every stats-window field as `(tag, instance_name)`."""
    origin = stats_origin()
    ids = iter(range(CHAR_STATS_FIELD_FIRST, CHAR_STATS_FIELD_FIRST + 200))
    out = [(field(next(ids), line_in(CAPTION), origin, colors, 'header', ALIGN_CENTER),
            'Title')]
    for row in range(len(BAR_COLORS)):
        x, y, _w, _h = row_rect(DYNAMIC_BOX, BOX_PAD + row * ROW_H)
        out.append((field(next(ids), line_in((x, y, BAR_LABEL_W, ROW_H)),
                          origin, colors), f'BarLabel{row}'))
        out.append((field(next(ids), line_in(bar_rect(row)), origin, colors,
                          align=ALIGN_CENTER), f'BarValue{row}'))
    out += paired_rows(ids, [row_rect(INFO_BOX, y) for y in INFO_ROWS], 'Info', colors)
    out += paired_rows(ids, [row_rect(ATTRIBUTE_BOX, BOX_PAD + r * ROW_H)
                             for r in range(ATTRIBUTE_ROWS)], 'Attr', colors)
    x, y, w, _h = skill_view()
    out += paired_rows(ids, [(x, y + r * ROW_H, w, ROW_H)
                             for r in range(SKILL_FIELDS)], 'Skill', colors)
    out += [(field(next(ids), line_in(client(tab)), origin, colors, align=ALIGN_CENTER), f'Tab{i}')
            for i, tab in enumerate(TABS)]
    return out


def stats_sprites(art) -> list:
    """The caption caps, the skill scrollbar and its thumb."""
    origin, bar = stats_origin(), skill_scroll()
    parts = [
        (art.compose_cap('right'), 'CapLeft', CAPTION),
        (art.compose_cap('left'), 'CapRight', CAPTION),
        (art.compose_scrollbar(bar[3]), 'SkillScroll', bar),
        (art.compose_thumb(THUMB_W, THUMB_H), 'SkillThumb',
         (bar[0] + THUMB_X, bar[1] + SCROLL_TRACK[0], THUMB_W, THUMB_H)),
    ]
    return [sprite(CHAR_STATS_SPRITE_FIRST + 3 * i, image, name, rect, origin)
            for i, (image, name, rect) in enumerate(parts)]


def stats_window(art) -> Swf:
    """The stats window movie.

    See: docs/plans/character_sheet.md#m2-swf
    """
    origin = stats_origin()
    tags = chrome_tags(CHAR_STATS_BMP, compose_stats(art), origin)
    tags += cover(CHAR_COVER, 'Cover', CAPTION, origin, 2, art.cover)
    for row in range(len(BAR_COLORS)):
        tags += cover(CHAR_BAR_COVER_FIRST + 2 * row, f'BarCover{row}',
                      bar_fill_rect(row), origin, 3 + row)
    sprites, fields = stats_sprites(art), stats_fields(art.colors)
    depth = 3 + len(BAR_COLORS)
    tags += place_all(sprites, fields, depth)
    tags += tip_tags(art, depth + len(sprites) + len(fields))
    return movie(tags + style_marker(art))


# ---------------------------------------------------------------------------
# Level-up dialog
# ---------------------------------------------------------------------------

def grid_row(attribute: int) -> tuple:
    """`(column_x, row_y)` of an attribute's grid row, in DIALOG space."""
    column, row = divmod(attribute, PER_COLUMN)
    return ASSIGN[0] + COLUMNS[column], ASSIGN[1] + row * GRID_ROW_H


def compose_dialog(art):
    """The level-up dialog's chrome: frame, the image box and the OK button."""
    panel = art.compose_frame(DIALOG_W, DIALOG_H, fill=art.background)
    bx, by, bw, bh = IMAGE_BOX
    panel.alpha_composite(art.compose_box(bw, bh), (bx, by))
    ox, oy, ow, oh = OK_BUTTON
    panel.alpha_composite(art.compose_button(ow, oh), (ox, oy))
    return panel


def ok_caption_rect() -> tuple:
    """The OK caption box, in DIALOG space."""
    x, y, w, h = OK_BUTTON
    return line_in((x + BUTTON_INSET[0], y + BUTTON_INSET[1],
                    w - 2 * BUTTON_INSET[0], h - 2 * BUTTON_INSET[1]))


def grid_fields(ids, colors: dict) -> list:
    """Each attribute's multiplier, name and value fields, at its grid row."""
    origin, out = dialog_origin(), []
    for attribute in range(ATTRIBUTE_ROWS):
        x, y = grid_row(attribute)
        name_x = x + MULTIPLIER_W
        out += [(field(next(ids), line_in((x, y, MULTIPLIER_FIELD_W, GRID_ROW_H)),
                       origin, colors), f'Mult{attribute}'),
                (field(next(ids), line_in((name_x, y, NAME_W, GRID_ROW_H)),
                       origin, colors), f'AttrName{attribute}'),
                (field(next(ids), line_in((name_x, y, VALUE_W, GRID_ROW_H)),
                       origin, colors, 'header'), f'AttrValue{attribute}')]
    return out


def dialog_fields(colors: dict) -> list:
    """Every level-up field as `(tag, instance_name)`."""
    origin = dialog_origin()
    ids = iter(range(CHAR_DIALOG_FIELD_FIRST, CHAR_DIALOG_FIELD_FIRST + 100))
    out = [(field(next(ids), line_in(LEVEL_TEXT), origin, colors, align=ALIGN_CENTER),
            'LevelText'),
           (field(next(ids), DESCRIPTION, origin, colors), 'Description'),
           (field(next(ids), ok_caption_rect(), origin, colors, align=ALIGN_CENTER),
            'OkCaption')]
    return out + grid_fields(ids, colors)


def dialog_sprites(art) -> list:
    """The gold coins, then every class image the art has, each a sprite of its own."""
    origin, coin = dialog_origin(), art.icons.coin().resize((COIN, COIN))
    out = [sprite(CHAR_COIN_FIRST + 3 * i, coin, f'Coin{i}',
                  (COIN_ROW[0], COIN_ROW[1], COIN, COIN), origin)
           for i in range(COINS)]
    for i, name in enumerate(CLASSES):
        image = art.icons.class_image(name)
        if image:
            out.append(sprite(CHAR_CLASS_FIRST + 3 * i, image, f'Class_{name}',
                              IMAGE, origin, CLASS_PIXELS))
    return out


def levelup_dialog(art) -> Swf:
    """The level-up dialog movie.

    See: docs/plans/character_sheet.md#m2-swf
    """
    tags = chrome_tags(CHAR_DIALOG_BMP, compose_dialog(art), dialog_origin())
    sprites, fields = dialog_sprites(art), dialog_fields(art.colors)
    tags += place_all(sprites, fields, 2)
    tags += tip_tags(art, 2 + len(sprites) + len(fields))
    return movie(tags + style_marker(art))


# ---------------------------------------------------------------------------
# The perks menu's Character button
# ---------------------------------------------------------------------------

def rounded(rect: tuple, rgba: tuple):
    """One rounded rectangle of `rgba`, drawn BUTTON_SUPERSAMPLE times `rect`'s size."""
    w, h = rect[2] * BUTTON_SUPERSAMPLE, rect[3] * BUTTON_SUPERSAMPLE
    image = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle((0, 0, w - 1, h - 1), radius=4 * BUTTON_SUPERSAMPLE,
                                            fill=rgba)
    return image


def button_text(char_id: int, rect: tuple, px: int, rgb: tuple, align: int) -> Tag:
    """A field in the perks menu's own face at a button-space rect."""
    return define_edit_text(char_id, *rect, '', '', font_id=CHAR_FONT, rgb=rgb,
                            height=px * TWIP, align=align)


def perks_button(_art) -> Swf:
    """The key hint the plugin lays over Skyrim's perks menu, in that menu's
    own look whatever the sheet's style: one `Button` sprite holding `Glow`,
    the key cap, `Key` and `Label`, which the plugin moves to the screen's
    bottom-left corner.

    See: docs/commentary/morrowind_runtime.md#perks-button
    """
    first = CHAR_BUTTON_FIRST
    key = rounded(BUTTON_KEY, (190, 190, 190, 255))
    glow = rounded(BUTTON_GLOW, (255, 255, 255, 38))
    tags = [define_bits_lossless2(first, *key.size, premultiplied_argb(key)),
            define_shape3_bitmap_rects(first + 1, [(first, *BUTTON_KEY, key.size)]),
            define_bits_lossless2(first + 2, *glow.size, premultiplied_argb(glow)),
            define_shape3_bitmap_rects(first + 3, [(first + 2, *BUTTON_GLOW, glow.size)]),
            define_sprite(first + 4, [place_object2(depth=1, character_id=first + 3)]),
            button_text(first + 5, BUTTON_KEY, BUTTON_KEY_PX, BUTTON_INK, ALIGN_CENTER),
            button_text(first + 6, BUTTON_LABEL, BUTTON_LABEL_PX, BUTTON_GRAY, ALIGN_LEFT)]
    parts = ((first + 4, 'Glow'), (first + 1, 'Cap'), (first + 5, 'Key'), (first + 6, 'Label'))
    tags.append(define_sprite(first + 7, [place_object2(depth=depth, character_id=char, name=name)
                                          for depth, (char, name) in enumerate(parts, 1)]))
    tags.append(place_object2(depth=1, character_id=first + 7, name='Button'))
    return movie(tags, import_font())


# ---------------------------------------------------------------------------
# The plugin's header and the CLI
# ---------------------------------------------------------------------------

def rect_lines(prefix: str, rect: tuple, origin: tuple) -> list:
    """Four `constexpr int` lines for one window-space rect, on the stage."""
    return [f'constexpr int k{prefix}{axis} = {value};'
            for axis, value in zip('XYWH', on_stage(rect, origin))]


def stats_lines() -> list:
    """The stats window's rects, row counts and pitch."""
    origin = stats_origin()
    lines = rect_lines('Caption', CAPTION, origin)
    for row in range(len(BAR_COLORS)):
        lines += rect_lines(f'BarFill{row}', bar_fill_rect(row), origin)
    lines += rect_lines('SkillView', skill_view(), origin)
    lines += rect_lines('SkillScroll', skill_scroll(), origin)
    lines += rect_lines('AttrRow', row_rect(ATTRIBUTE_BOX, BOX_PAD), origin)
    lines += rect_lines('LevelRow', row_rect(INFO_BOX, INFO_ROWS[0]), origin)
    for i, tab in enumerate(TABS):
        lines += rect_lines(f'Tab{i}', client(tab), origin)
    return lines + [f'constexpr int kCaptionPad = {CAPTION_PAD};',
                    f'constexpr int kRowH = {ROW_H};',
                    f'constexpr int kBarRows = {len(BAR_COLORS)};',
                    f'constexpr int kInfoRows = {len(INFO_ROWS)};',
                    f'constexpr int kAttributeRows = {ATTRIBUTE_ROWS};',
                    f'constexpr int kSkillFields = {SKILL_FIELDS};']


def dialog_lines() -> list:
    """The level-up dialog's rects, grid and coin geometry, and class names."""
    origin = dialog_origin()
    x, y = grid_row(0)
    lines = rect_lines('Ok', OK_BUTTON, origin)
    lines += rect_lines('CoinRow', COIN_ROW, origin)
    lines += [f'constexpr int kGridX = {x + origin[0]};',
              f'constexpr int kGridY = {y + origin[1]};',
              f'constexpr int kGridColumnStep = {COLUMNS[1] - COLUMNS[0]};',
              f'constexpr int kGridRowH = {GRID_ROW_H};',
              f'constexpr int kGridPerColumn = {PER_COLUMN};',
              f'constexpr int kMultiplierW = {MULTIPLIER_W};',
              f'constexpr int kNameW = {NAME_W};',
              f'constexpr int kCoin = {COIN};',
              f'constexpr int kCoinSpacing = {COIN_SPACING};',
              f'constexpr int kCoins = {COINS};']
    names = ', '.join(f'"{name}"' for name in CLASSES)
    return lines + [f'constexpr const char* kClassImages[] = {{{names}}};',
                    f'constexpr int kClassImageCount = {len(CLASSES)};']


def tip_lines() -> list:
    """The stage, and the tooltip's geometry inside its box."""
    return [f'constexpr int kStageW = {STAGE_W};',
            f'constexpr int kStageH = {STAGE_H};',
            f'constexpr int kTipW = {TIP_W};',
            f'constexpr int kTipPad = {TIP_PAD};',
            f'constexpr int kTipTop = {TIP_TOP};',
            f'constexpr int kTipNameX = {tip_name_x()};',
            f'constexpr int kTipNameDy = {text_top(TIP_PAD, TIP_ICON)};',
            f'constexpr int kTipSkillNameDy = {text_top(TIP_PAD, TIP_LINE)};',
            f'constexpr int kTipAttrDy = {text_top(TIP_PAD + TIP_LINE, TIP_LINE)};',
            f'constexpr int kTipGap = {TIP_GAP};',
            f'constexpr int kTipLabelH = {TIP_LABEL_H};',
            f'constexpr int kTipLabelDy = {text_top(0, TIP_LABEL_H)};',
            f'constexpr int kTipBarW = {TIP_BAR_W};',
            f'constexpr int kTipBarH = {TIP_BAR_H};',
            f'constexpr int kTipBarTextDy = {text_top(0, TIP_BAR_H)};',
            f'constexpr int kTipIcons = {ATTRIBUTES};',
            f'constexpr int kTipSkillFirst = {SKYRIM_SKILLS[0]};',
            f'constexpr int kTipSkillIcons = {len(SKYRIM_SKILLS)};']


def button_lines() -> list:
    """The button's size, its place in the perks menu's bar, and its text sizes."""
    return [f'constexpr int kButtonW = {BUTTON_GLOW[2]};',
            f'constexpr int kButtonH = {BUTTON_GLOW[3]};',
            f'constexpr int kButtonMargin = {BUTTON_MARGIN};',
            f'constexpr int kPerksBarH = {PERKS_BAR_H};',
            'constexpr unsigned kButtonGray = 0x%02x%02x%02x;' % BUTTON_GRAY]


def layout_header() -> str:
    """The plugin's copy of every layout, generated so nothing can disagree."""
    lines = ['// GENERATED by tools/generators/gen_morrowind_stats_swf.py.',
             '// Edit the generator, not this file.', '', '#pragma once', '',
             'namespace tesruntime::mw::stats_layout {', '']
    lines += stats_lines() + [''] + dialog_lines() + [''] + tip_lines() + [''] + button_lines()
    lines += ['', '}  // namespace tesruntime::mw::stats_layout', '']
    return '\n'.join(lines)


def write_movies(art, out_dir: str, compress: bool = True) -> list:
    """Every movie in `art`'s look into `out_dir`; returns the paths written."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, build in ((STATS_MOVIE, stats_window),
                        (LEVELUP_MOVIE, levelup_dialog),
                        (BUTTON_MOVIE, perks_button)):
        path = os.path.join(out_dir, name)
        with open(path, 'wb') as fh:
            fh.write(build(art).serialize(compress=compress))
        written.append(path)
    return written


def main() -> None:
    """CLI: write both movies and the plugin's layout header."""
    ap = argparse.ArgumentParser(
        description='Author the Morrowind stats and level-up SWFs.')
    ap.add_argument('--out', default='tes_runtime/morrowind/interface')
    ap.add_argument('--header', default=HEADER_PATH)
    ap.add_argument('--export-root', default='export')
    ap.add_argument('--style', choices=STYLES, default=None)
    ap.add_argument('--icons', choices=ICON_SETS, default=None)
    ap.add_argument('--uncompressed', action='store_true')
    args = ap.parse_args()
    art = menu_art(args.export_root, args.style, args.icons)
    for path in write_movies(art, args.out, not args.uncompressed):
        print(f'wrote {path} ({os.path.getsize(path)} bytes)')
    with open(args.header, 'w', encoding='ascii', newline='\n') as fh:
        fh.write(layout_header())
    print(f'layout -> {args.header}')


if __name__ == '__main__':
    main()

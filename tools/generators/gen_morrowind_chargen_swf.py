"""
Author the Morrowind class and birthsign menu SWFs, and their layout header.

Two standalone movies drawn like the level-up dialog
(`gen_morrowind_stats_swf.py`): the art object's chrome composed into one
bitmap, the embedded face, and every string a field the plugin writes.

- `morrowind_class.swf`: OpenMW's `openmw_chargen_class.layout` without the
  major and minor skills. Their place holds the class description, or, for a
  custom class, its name in an edit box (OpenMW's CreateClassDialog) and the
  eight attributes to pick two favored ones from.
- `morrowind_birth.swf`: `openmw_chargen_birth.layout`, its spell area holding
  BIRTH_LINES lines the plugin writes as OpenMW's BirthDialog does: headers,
  spell names, and effects indented past their icon.

A list shows LIST_ROWS rows the plugin fills; it scrolls by whole rows. Every
class picture and every birthsign picture the art has is a hidden sprite, and
every effect icon the shared table shows waits off the stage once per line.

See: docs/commentary/morrowind_runtime.md#chargen-menus
"""

import argparse
import os

from asset_convert.ui.menu_art import ICON_SETS, STYLES, effect_icons, menu_art
from core.chargen_source import shared_lines
from asset_convert.ui.morrowind_menu_art import SCROLL_TRACK, SCROLL_W
from tools.generators.gen_morrowind_menu_swf import (ALIGN_CENTER, STAGE_H, STAGE_W, THUMB_H,
                                                     THUMB_W, THUMB_X, style_marker)
from asset_convert.ui.swf import place_object2
from tools.generators.gen_morrowind_stats_swf import (BUTTON_INSET, CLASS_PIXELS, CLASSES,
                                                      chrome_tags, field, line_in, movie,
                                                      place_all, rect_lines, sprite)

#: Where the plugin's copy of both layouts is written.
HEADER_PATH = 'tes_runtime/morrowind/plugin/chargen_layout.h'

#: The two movies, as the game loads them under Interface/.
CLASS_MOVIE = 'morrowind_class.swf'
BIRTH_MOVIE = 'morrowind_birth.swf'

#: A list row's height, the rows a list shows, and the pad inside its box.
ROW_H = 18
LIST_ROWS = 7
PAD = 4

#: The space between a window's edge and what it holds, on every side.
MARGIN = 8

#: OpenMW's PickClassDialog list and picture boxes and the picture; the window ends a margin past them.
CLASS_LIST = (8, 8, 194, 138)
CLASS_IMAGE_BOX = (210, 8, 265, 138)
CLASS_IMAGE = (212, 10, 261, 134)
CLASS_W = CLASS_IMAGE_BOX[0] + CLASS_IMAGE_BOX[2] + MARGIN
CLASS_OK = (CLASS_W - MARGIN - 64, 276, 64, 24)
CLASS_H = CLASS_OK[1] + CLASS_OK[3] + MARGIN

#: The specialization and favored-attribute lines under the list, as OpenMW places them.
CLASS_LINES = {'SpecHeader': (8, 156, 166, 18), 'SpecName': (8, 174, 166, 18),
               'FavHeader': (8, 195, 166, 18), 'Fav0': (8, 213, 166, 18),
               'Fav1': (8, 231, 166, 18)}

#: Where the major and minor skills were: the description, or a custom class's name, header and picks.
CLASS_RIGHT = (184, 156, 291, 112)
PICK_COLUMNS = (184, 330)
PICK_W = 140
ATTRIBUTES = 8

#: A custom class's name: the label, and the edit box beside it holding the text.
NAME_LABEL_W = 56
NAME_BOX = (CLASS_RIGHT[0] + NAME_LABEL_W, CLASS_RIGHT[1], CLASS_RIGHT[2] - NAME_LABEL_W, 24)
NAME_INSET = 6

#: The favored-attribute header under the name box, and the picks' first row under it.
PICK_HEADER_Y = NAME_BOX[1] + NAME_BOX[3] + 4
PICK_TOP = PICK_HEADER_Y + ROW_H

#: OpenMW's BirthDialog list and picture boxes, the picture, the spell area and OK; a margin past them.
BIRTH_LIST = (8, 8, 232, 137)
BIRTH_IMAGE_BOX = (248, 8, 263, 137)
BIRTH_IMAGE = (250, 10, 259, 133)
BIRTH_TEXT = (8, 160, 507, 170)
BIRTH_W = BIRTH_TEXT[0] + BIRTH_TEXT[2] + MARGIN
BIRTH_OK = (BIRTH_W - MARGIN - 64, 338, 64, 24)
BIRTH_H = BIRTH_OK[1] + BIRTH_OK[3] + MARGIN

#: The spell area's lines (the most any sign has is 8); an effect's icon and text as OpenMW's MW_EffectImage.
BIRTH_LINES = 9
ICON_SIZE = 16
ICON_LEFT = 4
ICON_INDENT = 24
ICON_TOP = (ROW_H - ICON_SIZE) // 2

#: How far left of the stage each birthsign picture and effect icon waits until the plugin moves one in.
OFFSTAGE = 4000

#: Character ids: chrome, list, class pictures, name box, sign pictures, effect icons (3 each), fields.
CHAR_BMP = 20
CHAR_LIST_FIRST = 30
CHAR_CLASS_FIRST = 60
CHAR_NAME_BOX = 180
CHAR_SIGN_FIRST = 200
CHAR_FIELD_FIRST = 500
CHAR_ICON_FIRST = 700


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------

def origin(width: int, height: int) -> tuple:
    """A window's top-left on the stage: centered."""
    return ((STAGE_W - width) // 2, (STAGE_H - height) // 2)


def list_view(box: tuple) -> tuple:
    """A list's rows area inside its box, beside the scrollbar."""
    x, y, w, h = box
    return (x + PAD, y + PAD, w - 2 * PAD - SCROLL_W - 2, h - 2 * PAD)


def list_scroll(box: tuple) -> tuple:
    """A list's scrollbar, at its box's inner right edge."""
    x, y, w, h = box
    return (x + w - PAD - SCROLL_W, y + PAD, SCROLL_W, h - 2 * PAD)


def button_caption(button: tuple) -> tuple:
    """A button's caption box."""
    x, y, w, h = button
    return line_in((x + BUTTON_INSET[0], y + BUTTON_INSET[1],
                    w - 2 * BUTTON_INSET[0], h - 2 * BUTTON_INSET[1]))


def compose(art, size: tuple, boxes: list, ok: tuple):
    """A window's chrome: frame, inset boxes and the OK button."""
    panel = art.compose_frame(*size, fill=art.background)
    for bx, by, bw, bh in boxes:
        panel.alpha_composite(art.compose_box(bw, bh), (bx, by))
    panel.alpha_composite(art.compose_button(ok[2], ok[3]), ok[:2])
    return panel


def list_sprites(art, box: tuple, at: tuple, first: int) -> list:
    """The list's scrollbar and its thumb."""
    bar = list_scroll(box)
    parts = [(art.compose_scrollbar(bar[3]), 'ListScroll', bar),
             (art.compose_thumb(THUMB_W, THUMB_H), 'ListThumb',
              (bar[0] + THUMB_X, bar[1] + SCROLL_TRACK[0], THUMB_W, THUMB_H))]
    return [sprite(first + 3 * i, image, name, rect, at)
            for i, (image, name, rect) in enumerate(parts)]


def picture_sprites(pictures: dict, prefix: str, rect: tuple, at: tuple, first: int,
                    shift: int = 0) -> list:
    """Each picture as a sprite `<prefix><key>` stretched over `rect`, `shift` px to its left."""
    parts = [sprite(first + 3 * i, image, prefix + key, rect, at, CLASS_PIXELS)
             for i, (key, image) in enumerate(sorted(pictures.items()))]
    return [part[:3] + [(part[3][0], part[3][1], (part[3][2][0] - shift, part[3][2][1]))]
            for part in parts]


def row_fields(ids, box: tuple, at: tuple, colors: dict) -> list:
    """The list's row fields, `Row<i>`."""
    x, y, w, _h = list_view(box)
    return [(field(next(ids), line_in((x, y + r * ROW_H, w, ROW_H)), at, colors), f'Row{r}')
            for r in range(LIST_ROWS)]


def ok_field(ids, ok: tuple, at: tuple, colors: dict) -> list:
    """The OK caption."""
    return [(field(next(ids), button_caption(ok), at, colors, align=ALIGN_CENTER), 'OkCaption')]


# ---------------------------------------------------------------------------
# Class menu
# ---------------------------------------------------------------------------

def pick_rect(attribute: int) -> tuple:
    """A custom class's attribute pick row, under the name box and the picks' header."""
    column, row = divmod(attribute, ATTRIBUTES // len(PICK_COLUMNS))
    return (PICK_COLUMNS[column], PICK_TOP + ROW_H * row, PICK_W, ROW_H)


def name_fields(ids, at: tuple, colors: dict) -> list:
    """A custom class's name label, the text inside its box, and the picks' header."""
    x, y, w, h = NAME_BOX
    return [(field(next(ids), line_in((CLASS_RIGHT[0], y, NAME_LABEL_W, h)), at, colors),
             'NameLabel'),
            (field(next(ids), line_in((x + NAME_INSET, y, w - 2 * NAME_INSET, h)), at, colors),
             'NameText'),
            (field(next(ids), line_in((CLASS_RIGHT[0], PICK_HEADER_Y, CLASS_RIGHT[2], ROW_H)),
                   at, colors, 'header'), 'PickHeader')]


def class_fields(colors: dict) -> list:
    """Every class-window field as `(tag, instance_name)`."""
    at = origin(CLASS_W, CLASS_H)
    ids = iter(range(CHAR_FIELD_FIRST, CHAR_FIELD_FIRST + 100))
    out = row_fields(ids, CLASS_LIST, at, colors)
    for name, rect in CLASS_LINES.items():
        color = 'header' if name.endswith('Header') else 'normal'
        out.append((field(next(ids), line_in(rect), at, colors, color), name))
    out.append((field(next(ids), CLASS_RIGHT, at, colors), 'Description'))
    out += name_fields(ids, at, colors)
    out += [(field(next(ids), line_in(pick_rect(a)), at, colors), f'Pick{a}')
            for a in range(ATTRIBUTES)]
    return out + ok_field(ids, CLASS_OK, at, colors)


def class_window(art):
    """The class menu movie."""
    at = origin(CLASS_W, CLASS_H)
    tags = chrome_tags(CHAR_BMP, compose(art, (CLASS_W, CLASS_H), [CLASS_LIST, CLASS_IMAGE_BOX],
                                         CLASS_OK), at)
    pictures = {name: art.icons.class_image(name) for name in CLASSES}
    sprites = (list_sprites(art, CLASS_LIST, at, CHAR_LIST_FIRST)
               + picture_sprites({k: v for k, v in pictures.items() if v}, 'Class_',
                                 CLASS_IMAGE, at, CHAR_CLASS_FIRST)
               + [sprite(CHAR_NAME_BOX, art.compose_box(*NAME_BOX[2:]), 'NameBox', NAME_BOX, at)])
    tags += place_all(sprites, class_fields(art.colors), 2)
    return movie(tags + style_marker(art))


# ---------------------------------------------------------------------------
# Birthsign menu
# ---------------------------------------------------------------------------

def birth_line(row: int) -> tuple:
    """Spell-area line `row` (an effect's text starts ICON_INDENT further in)."""
    x, y, w, _h = BIRTH_TEXT
    return (x + PAD, y + PAD + ROW_H * row, w - 2 * PAD - ICON_INDENT, ROW_H)


def birth_fields(colors: dict) -> list:
    """Every birthsign-window field as `(tag, instance_name)`."""
    at = origin(BIRTH_W, BIRTH_H)
    ids = iter(range(CHAR_FIELD_FIRST, CHAR_FIELD_FIRST + 100))
    out = row_fields(ids, BIRTH_LIST, at, colors)
    out += [(field(next(ids), line_in(birth_line(r)), at, colors), f'Line{r}')
            for r in range(BIRTH_LINES)]
    return out + ok_field(ids, BIRTH_OK, at, colors)


def icon_tags(icons: dict, at: tuple, depth: int) -> list:
    """Each effect icon defined once and placed on every line as `Icon<row>_<key>`,
    ICON_SIZE square, OFFSTAGE px left of its line until the plugin moves it in."""
    tags = []
    x, y, _w, _h = birth_line(0)
    for i, (key, image) in enumerate(sorted(icons.items())):
        part = sprite(CHAR_ICON_FIRST + 3 * i, image, '', (x + ICON_LEFT, y, ICON_SIZE, ICON_SIZE),
                      at, image.size)
        tags += part[:3]
        sprite_id, _name, (left, top) = part[3]
        for row in range(BIRTH_LINES):
            tags.append(place_object2(depth=depth, character_id=sprite_id, name=f'Icon{row}_{key}',
                                      translate=(left - OFFSTAGE, top + ROW_H * row + ICON_TOP)))
            depth += 1
    return tags


def birth_window(art, icons: dict = None):
    """The birthsign menu movie; `icons` are the effect icons, `{key: image}`."""
    at = origin(BIRTH_W, BIRTH_H)
    tags = chrome_tags(CHAR_BMP, compose(art, (BIRTH_W, BIRTH_H),
                                         [BIRTH_LIST, BIRTH_IMAGE_BOX, BIRTH_TEXT], BIRTH_OK), at)
    sprites = (list_sprites(art, BIRTH_LIST, at, CHAR_LIST_FIRST)
               + picture_sprites(art.icons.birthsigns(), 'Sign_', BIRTH_IMAGE, at,
                                 CHAR_SIGN_FIRST, OFFSTAGE))
    fields = birth_fields(art.colors)
    tags += place_all(sprites, fields, 2)
    tags += icon_tags(icons or {}, at, 2 + len(sprites) + len(fields))
    return movie(tags + style_marker(art))


# ---------------------------------------------------------------------------
# The plugin's header and the CLI
# ---------------------------------------------------------------------------

def window_lines(prefix: str, size: tuple, box: tuple, ok: tuple) -> list:
    """One window's list rows, scrollbar and OK rects, on the stage."""
    at = origin(*size)
    return (rect_lines(f'{prefix}ListView', list_view(box), at)
            + rect_lines(f'{prefix}ListScroll', list_scroll(box), at)
            + rect_lines(f'{prefix}Ok', ok, at))


def layout_header() -> str:
    """The plugin's copy of both layouts, generated so nothing can disagree."""
    at = origin(CLASS_W, CLASS_H)
    lines = ['// GENERATED by tools/generators/gen_morrowind_chargen_swf.py.',
             '// Edit the generator, not this file.', '', '#pragma once', '',
             'namespace tesruntime::mw::chargen_layout {', '',
             f'constexpr int kRowH = {ROW_H};', f'constexpr int kListRows = {LIST_ROWS};', '']
    lines += window_lines('Class', (CLASS_W, CLASS_H), CLASS_LIST, CLASS_OK)
    lines += rect_lines('Pick0', pick_rect(0), at)
    lines += [f'constexpr int kPickColumnStep = {PICK_COLUMNS[1] - PICK_COLUMNS[0]};',
              f'constexpr int kPickPerColumn = {ATTRIBUTES // len(PICK_COLUMNS)};', '']
    birth = origin(BIRTH_W, BIRTH_H)
    lines += window_lines('Birth', (BIRTH_W, BIRTH_H), BIRTH_LIST, BIRTH_OK)
    lines += rect_lines('BirthImage', BIRTH_IMAGE, birth)
    lines += rect_lines('BirthLine0', birth_line(0), birth)
    lines += [f'constexpr int kBirthLines = {BIRTH_LINES};',
              f'constexpr int kIconLeft = {ICON_LEFT};',
              f'constexpr int kIconIndent = {ICON_INDENT};',
              f'constexpr int kOffstage = {OFFSTAGE};']
    lines += ['', '}  // namespace tesruntime::mw::chargen_layout', '']
    return '\n'.join(lines)


def write_movies(art, out_dir: str, compress: bool = True, icons: dict = None) -> list:
    """Both movies in `art`'s look into `out_dir`, the birth menu with effect
    `icons` (`{key: image}`); returns the paths written."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, built in ((CLASS_MOVIE, class_window(art)), (BIRTH_MOVIE, birth_window(art, icons))):
        path = os.path.join(out_dir, name)
        with open(path, 'wb') as fh:
            fh.write(built.serialize(compress=compress))
        written.append(path)
    return written


def main() -> None:
    """CLI: write both movies and the plugin's layout header."""
    ap = argparse.ArgumentParser(description='Author the Morrowind class and birthsign SWFs.')
    ap.add_argument('--out', default='tes_runtime/morrowind/interface')
    ap.add_argument('--header', default=HEADER_PATH)
    ap.add_argument('--export-root', default='export')
    ap.add_argument('--style', choices=STYLES, default=None)
    ap.add_argument('--icons', choices=ICON_SETS, default=None)
    ap.add_argument('--uncompressed', action='store_true')
    args = ap.parse_args()
    art = menu_art(args.export_root, args.style, args.icons)
    icons = effect_icons(args.export_root, shared_lines(args.export_root)[2])
    for path in write_movies(art, args.out, not args.uncompressed, icons):
        print(f'wrote {path} ({os.path.getsize(path)} bytes)')
    with open(args.header, 'w', encoding='ascii', newline='\n') as fh:
        fh.write(layout_header())
    print(f'layout -> {args.header}')


if __name__ == '__main__':
    main()

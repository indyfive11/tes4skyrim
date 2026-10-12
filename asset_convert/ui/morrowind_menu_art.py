"""
Morrowind's own menu art, read from the player's install at build time.

🛑 NOTHING HERE IS COMMITTED. The textures are Bethesda's, so they are read
from the registered installs that hold them (`menu_art.art_sources`: Morrowind,
Arktwend, ...) and composed into the generated SWF,
which is itself a build artifact. The repo carries the LAYOUT (which texture
goes where, at what size) and never the pixels.

`MorrowindArt` binds it all to one install: the art object every menu
generator takes, which `skyrim_menu_art.SkyrimArt` stands in for.

See: docs/commentary/morrowind_runtime.md#the-real-menu
"""

from PIL import Image, ImageChops

from asset_convert.ui.ui_menus import to_image

#: `[FontColor]` from Morrowind.ini, under its own key names; the plugin gets this same table.
FONT_COLORS = {
    'normal': (202, 165, 96), 'normal_over': (223, 201, 159),
    'normal_pressed': (243, 237, 221), 'link': (112, 126, 207),
    'link_over': (143, 155, 218), 'link_pressed': (175, 184, 228),
    'answer': (150, 50, 30), 'answer_over': (223, 201, 159),
    'answer_pressed': (243, 237, 221), 'header': (223, 201, 159),
    'notify': (223, 201, 159), 'disabled': (179, 168, 135),
}

#: `[FontColor] color_background`, at the window's own alpha.
COLOR_BACKGROUND = (0, 0, 0, 245)

#: Border thickness in pixels; every `menu_thick_border_*` edge is 4 px.
BORDER = 4

#: MW_Box's thin border is 2 px, not 4.
BOX_BORDER = 2

#: The folders UI art is stored under: frames and bars, and item icons such as the gold coin.
TEXTURES = 'textures'
ICONS = 'icons'

#: The 9-slice window frame, keyed by the corner or edge each texture fills.
FRAME = {
    'top': 'menu_thick_border_top',
    'bottom': 'menu_thick_border_bottom',
    'left': 'menu_thick_border_left',
    'right': 'menu_thick_border_right',
    'tl': 'menu_thick_border_top_left_corner',
    'tr': 'menu_thick_border_top_right_corner',
    'bl': 'menu_thick_border_bottom_left_corner',
    'br': 'menu_thick_border_bottom_right_corner',
}

#: The thinner frame MW_Box uses for the inset panes.
BOX = {
    'top': 'menu_thin_border_top',
    'bottom': 'menu_thin_border_bottom',
    'left': 'menu_thin_border_left',
    'right': 'menu_thin_border_right',
    'tl': 'menu_thin_border_top_left_corner',
    'tr': 'menu_thin_border_top_right_corner',
    'bl': 'menu_thin_border_bottom_left_corner',
    'br': 'menu_thin_border_bottom_right_corner',
}

#: HB_ALL, the patterned caption plate: the title bar and the Goodbye button.
HEAD = {
    'top': 'menu_head_block_top',
    'bottom': 'menu_head_block_bottom',
    'left': 'menu_head_block_left',
    'right': 'menu_head_block_right',
    'tl': 'menu_head_block_top_left_corner',
    'tr': 'menu_head_block_top_right_corner',
    'bl': 'menu_head_block_bottom_left_corner',
    'br': 'menu_head_block_bottom_right_corner',
}

#: HB_ALL's edges are 2 px, and the plate is 20 px tall.
HEAD_BORDER = 2
HEAD_HEIGHT = 20

#: MW_Button's own frame, a 4 px border distinct from the window's.
BUTTON = {
    'top': 'menu_button_frame_top',
    'bottom': 'menu_button_frame_bottom',
    'left': 'menu_button_frame_left',
    'right': 'menu_button_frame_right',
    'tl': 'menu_button_frame_top_left_corner',
    'tr': 'menu_button_frame_top_right_corner',
    'bl': 'menu_button_frame_bottom_left_corner',
    'br': 'menu_button_frame_bottom_right_corner',
}


class MissingArtError(RuntimeError):
    """No registered install has such a texture."""


# ---------------------------------------------------------------------------
# The art, composed
# ---------------------------------------------------------------------------

def load(files, name: str, folder: str = TEXTURES):
    """One UI texture as a Pillow RGBA image, by its name under `folder` (no
    ext), read from `files` (`menu_art.FileChain`: the installs holding the art)."""
    data = files.read(folder + chr(92) + name + '.dds') if files else None
    if data is None:
        raise MissingArtError(
            f'{name}.dds not found -- register a Morrowind install, or the '
            f'menu cannot be built')
    return to_image(data)


def _edge(img, width: int, height: int):
    """An edge texture resampled to the run it has to cover."""
    return img.resize((max(1, width), max(1, height)), Image.LANCZOS)


def compose_frame(files, width: int, height: int, parts=None,
                  border: int = BORDER, fill=None):
    """Morrowind's bordered panel, composed into ONE `width` x `height` image.

    `fill` is the interior RGBA; None leaves it transparent so a pane can sit
    over the window's own background. Corners keep their authored pixels; only
    the four edges resample, along the one axis they run.
    See: docs/commentary/morrowind_runtime.md#one-bitmap
    """
    parts = parts or FRAME
    art = {key: load(files, name) for key, name in parts.items()}
    panel = Image.new('RGBA', (width, height), fill or (0, 0, 0, 0))

    inner_w = max(1, width - 2 * border)
    inner_h = max(1, height - 2 * border)
    panel.paste(_edge(art['top'], inner_w, border), (border, 0))
    panel.paste(_edge(art['bottom'], inner_w, border), (border, height - border))
    panel.paste(_edge(art['left'], border, inner_h), (0, border))
    panel.paste(_edge(art['right'], border, inner_h), (width - border, border))
    panel.paste(art['tl'].resize((border, border)), (0, 0))
    panel.paste(art['tr'].resize((border, border)), (width - border, 0))
    panel.paste(art['bl'].resize((border, border)), (0, height - border))
    panel.paste(art['br'].resize((border, border)),
                (width - border, height - border))
    return panel


def compose_box(files, width: int, height: int, fill=None):
    """MW_Box: the thin-bordered inset the topic list and history sit in."""
    return compose_frame(files, width, height, parts=BOX,
                         border=BOX_BORDER, fill=fill)


def _tile(img, width: int, height: int):
    """`img` repeated across `width`, resampled only in height, so a pattern
    keeps its pitch however long the run."""
    out = Image.new('RGBA', (max(1, width), max(1, height)), (0, 0, 0, 0))
    strip = img.resize((img.width, max(1, height)), Image.LANCZOS)
    for x in range(0, width, img.width):
        out.paste(strip, (x, 0))
    return out


def compose_head(files, width: int, height: int = HEAD_HEIGHT):
    """HB_ALL: the patterned plate behind the title and the Goodbye button.

    Its middle is `menu_head_block_middle` TILED rather than stretched -- that
    is the woven pattern running across the caption bar.
    """
    panel = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    middle = load(files, 'menu_head_block_middle')
    panel.paste(_tile(middle, width - 2 * HEAD_BORDER, height - 2 * HEAD_BORDER),
                (HEAD_BORDER, HEAD_BORDER))
    panel.alpha_composite(compose_frame(files, width, height,
                                        parts=HEAD, border=HEAD_BORDER))
    return panel


def compose_button(files, width: int, height: int):
    """MW_Button: its own 4 px frame, over the window's dark background."""
    return compose_frame(files, width, height, parts=BUTTON,
                         border=BORDER)


#: MW_VScroll: 14 px wide, a 15 px arrow box at each end, the track box inset 18 px top and 17 bottom.
SCROLL_W = 14
SCROLL_END = 15
SCROLL_TRACK = (18, 17)

#: MW_ArrowUp/Down: the arrow (the drawn part of a 32 px texture) at 10x10, centered in its end box.
ARROW = 10

#: Opaque black, the fill behind every scroll part.
_BLACK = (0, 0, 0, 255)


def compose_scrollbar(files, height: int):
    """MW_VScroll without its thumb: arrow boxes at both ends, track between.

    The thumb is composed separately (`compose_thumb`) because it moves.
    """
    panel = Image.new('RGBA', (SCROLL_W, height), (0, 0, 0, 0))
    track_h = height - SCROLL_TRACK[0] - SCROLL_TRACK[1]
    panel.alpha_composite(compose_box(files, SCROLL_W, track_h,
                                      fill=_BLACK), (0, SCROLL_TRACK[0]))
    for name, top in (('menu_scroll_up', 0),
                      ('menu_scroll_down', height - SCROLL_END)):
        panel.alpha_composite(compose_box(files, SCROLL_W, SCROLL_END,
                                          fill=_BLACK), (0, top))
        art = load(files, name)
        arrow = art.crop(art.split()[-1].getbbox()).resize((ARROW, ARROW),
                                                           Image.LANCZOS)
        panel.alpha_composite(arrow, ((SCROLL_W - ARROW) // 2,
                                      top + (SCROLL_END - ARROW) // 2))
    return panel


def compose_thumb(files, width: int, height: int):
    """MW_ScrollTrackV: the thumb, a thin-bordered black block."""
    return compose_box(files, width, height, fill=_BLACK)


def compose_line(files, width: int):
    """MW_HLine: the 2 px rule that separates services from topics."""
    return _edge(load(files, 'menu_thin_border_top'), width, 2)


def compose_cap(files, side: str, height: int = HEAD_HEIGHT):
    """One 2 px end cap of HB_ALL; `side` is 'left' or 'right'."""
    return _edge(load(files, HEAD[side]), HEAD_BORDER, height)


def compose_stat_bar(files, width: int, height: int, rgb: tuple):
    """MW_Progress_Red/Blue/Green, full: `menu_bar_gray` tinted `rgb` inside a box.

    OpenMW's track skins color the gray bar with the ini's `color_health`,
    `color_magic` and `color_fatigue`; the value is drawn over it.
    """
    panel = Image.new('RGBA', (width, height), (0, 0, 0, 255))
    inner = (width - 2 * BOX_BORDER, height - 2 * BOX_BORDER)
    gray = _edge(load(files, 'menu_bar_gray').convert('RGBA'), *inner)
    tint = Image.new('RGBA', inner, (*rgb, 255))
    panel.paste(ImageChops.multiply(gray, tint), (BOX_BORDER, BOX_BORDER))
    panel.alpha_composite(compose_box(files, width, height))
    return panel


def compose_bar(files, width: int, height: int, fraction: float):
    """The disposition bar: `menu_bar_blue` filled to `fraction`, in a box.

    The fill is dimmed so the value printed over it stays readable; Morrowind
    prints the number across the whole bar, not just the filled part.
    """
    panel = Image.new('RGBA', (width, height), (0, 0, 0, 255))
    inner = width - 2 * BOX_BORDER
    filled = max(0, min(inner, round(inner * fraction)))
    if filled:
        bar = load(files, 'menu_bar_blue').convert('RGBA')
        dim = Image.new('RGBA', (filled, height - 2 * BOX_BORDER),
                        (0, 0, 0, 90))
        piece = _edge(bar, filled, height - 2 * BOX_BORDER)
        piece.alpha_composite(dim)
        panel.paste(piece, (BOX_BORDER, BOX_BORDER))
    panel.alpha_composite(compose_box(files, width, height))
    return panel


# ---------------------------------------------------------------------------
# The art object
# ---------------------------------------------------------------------------

#: Store<ESM::Attribute>'s icon per attribute, under icons\k, in TES3 order.
ATTRIBUTE_ICONS = ('attribute_strength', 'attribute_int', 'attribute_wilpower',
                   'attribute_agility', 'attribute_speed', 'attribute_endurance',
                   'attribute_personality', 'attribute_luck')

#: Each Skyrim skill (actor value 6..23) as Morrowind pictures it, under icons\k (OpenMW `mwworld/store.cpp`).
SKILL_ICONS = {6: 'combat_longblade', 7: 'combat_axe', 8: 'stealth_marksman',
               9: 'combat_block', 10: 'combat_armor', 11: 'combat_heavyarmor',
               12: 'stealth_lightarmor', 13: 'stealth_sneak', 14: 'stealth_security',
               15: 'stealth_sneak', 16: 'magic_alchemy', 17: 'stealth_speechcraft',
               18: 'magic_alteration', 19: 'magic_conjuration', 20: 'magic_destruction',
               21: 'magic_illusion', 22: 'magic_restoration', 23: 'magic_enchant'}

#: The cover the plugin slides behind a caption: opaque, over the window's own black.
COVER_RGBA = (0, 0, 0, 255)


class MorrowindIcons:
    """Morrowind's attribute and skill icons, level-up class images and gold coin."""

    def __init__(self, files):
        self.root = files

    def attribute(self, index: int):
        """Attribute `index`'s icon (TES3 order)."""
        return load(self.root, 'k' + chr(92) + ATTRIBUTE_ICONS[index], ICONS)

    def skill(self, av: int):
        """Skyrim skill `av`'s nearest Morrowind skill icon."""
        return load(self.root, 'k' + chr(92) + SKILL_ICONS[av], ICONS)

    def class_image(self, name: str):
        """The level-up picture for class image `name`, at its own 256x128."""
        return load(self.root, 'levelup' + chr(92) + name)

    def coin(self):
        """The gold coin the level-up dialog spends."""
        return load(self.root, 'tx_goldicon', ICONS)

    def birthsigns(self) -> dict:
        """{sign key: picture}; imported late, as menu_art imports this module."""
        from asset_convert.ui.menu_art import sign_pictures
        return sign_pictures(self.root, TEXTURES + chr(92) + 'birthsigns')


class MorrowindArt:
    """Morrowind's look: each `compose_*` above bound to one install, and its colors.

    `skyrim_menu_art.SkyrimArt` has the same members, so a menu changes style
    by being built with the other object.
    See: docs/commentary/morrowind_runtime.md#menu-styles
    """

    style = 'morrowind'
    colors = FONT_COLORS
    background = COLOR_BACKGROUND
    cover = COVER_RGBA

    def __init__(self, files, icons=None):
        self.root = files
        self.icons = icons or MorrowindIcons(files)

    def compose_frame(self, width: int, height: int, fill=None):
        """The window frame."""
        return compose_frame(self.root, width, height, fill=fill)

    def compose_box(self, width: int, height: int, fill=None):
        """An inset pane."""
        return compose_box(self.root, width, height, fill=fill)

    def compose_head(self, width: int, height: int = HEAD_HEIGHT):
        """The caption plate."""
        return compose_head(self.root, width, height)

    def compose_button(self, width: int, height: int):
        """A button."""
        return compose_button(self.root, width, height)

    def compose_scrollbar(self, height: int):
        """A scrollbar without its thumb."""
        return compose_scrollbar(self.root, height)

    def compose_thumb(self, width: int, height: int):
        """A scrollbar thumb."""
        return compose_thumb(self.root, width, height)

    def compose_line(self, width: int):
        """The list rule."""
        return compose_line(self.root, width)

    def compose_cap(self, side: str, height: int = HEAD_HEIGHT):
        """A caption end cap."""
        return compose_cap(self.root, side, height)

    def compose_stat_bar(self, width: int, height: int, rgb: tuple):
        """A full Health/Magicka/Fatigue bar."""
        return compose_stat_bar(self.root, width, height, rgb)

    def compose_bar(self, width: int, height: int, fraction: float):
        """The disposition bar."""
        return compose_bar(self.root, width, height, fraction)

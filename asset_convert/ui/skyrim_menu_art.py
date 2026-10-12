"""
Skyrim's menu look: the same parts `morrowind_menu_art.MorrowindArt` draws, so
a menu changes style by being built with this object instead.

Everything here is drawn, not read: thin light rules that fade out at their
ends, translucent black panels and white text,
after Skyrim's own message box and stats menu. No game art is needed, so it
builds with no Morrowind install; the icons come from whichever game supplies
them (`menu_art.find_icons`).

See: docs/commentary/morrowind_runtime.md#menu-styles
"""

from PIL import Image, ImageDraw

from asset_convert.ui.morrowind_menu_art import (HEAD_HEIGHT, SCROLL_END,
                                                 SCROLL_W)

#: The light gray every rule is drawn in, and how opaque a rule's middle is.
RULE_RGB = (220, 220, 220)
RULE_ALPHA = 210

#: The fainter second rule inside the frame, a pane's outline and a button's.
FAINT_ALPHA = 70
BOX_ALPHA = 110
BUTTON_ALPHA = 150

#: The share of a frame rule's length over which it fades in from each end.
FADE = 0.3

#: The frame rule's inset from the edge.
RULE_INSET = 3

#: The window's black: a little more see-through than Morrowind's.
BACKGROUND = (0, 0, 0, 230)

#: White text that greys when disabled, keyed like Morrowind.ini's [FontColor] so the plugin reads both styles alike.
FONT_COLORS = {
    'normal': (200, 200, 200), 'normal_over': (255, 255, 255),
    'normal_pressed': (255, 255, 255), 'link': (150, 175, 210),
    'link_over': (200, 215, 235), 'link_pressed': (230, 236, 245),
    'answer': (205, 120, 95), 'answer_over': (255, 255, 255),
    'answer_pressed': (255, 255, 255), 'header': (255, 255, 255),
    'notify': (255, 255, 255), 'disabled': (115, 115, 115),
}

#: The dark track behind every bar's fill, and the disposition bar's blue.
TRACK_RGBA = (0, 0, 0, 200)
DISPOSITION_RGB = (70, 100, 170)

#: How far each bar's fill is lightened at its top and darkened at its bottom.
BAR_SHADE = (1.25, 0.7)

#: The thumb's drawn width inside its sprite, and the arrows' half width.
THUMB_BAR = 5
ARROW = 4


def _ramp(i: int, n: int) -> float:
    """1 along most of a run of `n`, falling to 0 over FADE of it at each end."""
    span = max(1.0, n * FADE)
    return max(0.0, min(1.0, (i + 0.5) / span, (n - i - 0.5) / span))


def _rule(layer, x: int, y: int, length: int, alpha: int,
          across: bool = True, fade: bool = True) -> None:
    """A 1 px rule into `layer`, across (or down) from `x`, `y`."""
    for i in range(max(0, length)):
        a = round(alpha * (_ramp(i, length) if fade else 1.0))
        layer.putpixel((x + i, y) if across else (x, y + i), (*RULE_RGB, a))


def _over(panel, layer):
    """`layer` blended over `panel`, so a faded rule never cuts the fill."""
    panel.alpha_composite(layer)
    return panel


def _outline(width: int, height: int, alpha: int, fill=None):
    """A 1 px straight outline round a `fill`ed panel."""
    panel = Image.new('RGBA', (width, height), fill or (0, 0, 0, 0))
    layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((0, 0, width - 1, height - 1),
                                    outline=(*RULE_RGB, alpha))
    return _over(panel, layer)


def compose_frame(width: int, height: int, fill=None):
    """A filled window: a faded rule along top and bottom, a faint second rule
    inside each, faint sides. Without `fill` (an inner frame) just the faint
    outline."""
    if fill is None:
        return _outline(width, height, FAINT_ALPHA)
    panel = Image.new('RGBA', (width, height), fill)
    layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    for y in (RULE_INSET, height - 1 - RULE_INSET):
        _rule(layer, 0, y, width, RULE_ALPHA)
    for y in (RULE_INSET + 3, height - 4 - RULE_INSET):
        _rule(layer, 0, y, width, FAINT_ALPHA)
    for x in (0, width - 1):
        _rule(layer, x, 0, height, FAINT_ALPHA, across=False)
    return _over(panel, layer)


def compose_box(width: int, height: int, fill=None):
    """An inset pane: a straight faint outline."""
    return _outline(width, height, BOX_ALPHA, fill)


def compose_head(width: int, height: int = HEAD_HEIGHT):
    """The caption: one faded rule the plugin's cover parts round the title."""
    layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    _rule(layer, 0, height // 2, width, RULE_ALPHA)
    return layer


def compose_cap(side: str, height: int = HEAD_HEIGHT):
    """Nothing: the caption rule simply stops at the title's gap."""
    return Image.new('RGBA', (1, height), (0, 0, 0, 0))


def compose_button(width: int, height: int):
    """A button: a dark plate in a light outline."""
    return _outline(width, height, BUTTON_ALPHA, (0, 0, 0, 120))


def compose_scrollbar(height: int):
    """A thin track line with a small arrow at each end; the thumb moves on it."""
    layer = Image.new('RGBA', (SCROLL_W, height), (0, 0, 0, 0))
    mid = SCROLL_W // 2
    _rule(layer, mid, SCROLL_END, height - 2 * SCROLL_END, BOX_ALPHA,
          across=False, fade=False)
    draw = ImageDraw.Draw(layer)
    top, bottom = SCROLL_END // 2 - ARROW // 2, height - SCROLL_END // 2 + ARROW // 2
    draw.polygon([(mid - ARROW, top + ARROW), (mid + ARROW, top + ARROW), (mid, top)],
                 fill=(*RULE_RGB, RULE_ALPHA))
    draw.polygon([(mid - ARROW, bottom - ARROW), (mid + ARROW, bottom - ARROW),
                  (mid, bottom)], fill=(*RULE_RGB, RULE_ALPHA))
    return layer


def compose_thumb(width: int, height: int):
    """A slim light block, centered on the track line."""
    layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    left = (width - THUMB_BAR) // 2
    ImageDraw.Draw(layer).rectangle((left, 0, left + THUMB_BAR - 1, height - 1),
                                    fill=(*RULE_RGB, 170))
    return layer


def compose_line(width: int):
    """The list rule: a faded 1 px line in a 2 px strip."""
    layer = Image.new('RGBA', (width, 2), (0, 0, 0, 0))
    _rule(layer, 0, 0, width, BOX_ALPHA)
    return layer


def _shaded(width: int, height: int, rgb: tuple):
    """`rgb` lighter at the top and darker at the bottom, as Skyrim's meters."""
    out = Image.new('RGBA', (max(1, width), max(1, height)))
    top, bottom = BAR_SHADE
    for y in range(out.height):
        k = top + (bottom - top) * y / max(1, out.height - 1)
        out.paste((*(min(255, round(c * k)) for c in rgb), 255),
                  (0, y, out.width, y + 1))
    return out


def compose_stat_bar(width: int, height: int, rgb: tuple):
    """A full meter: the shaded fill 2 px inside a dark track and outline."""
    panel = _outline(width, height, BOX_ALPHA, TRACK_RGBA)
    panel.alpha_composite(_shaded(width - 4, height - 4, rgb), (2, 2))
    return panel


def compose_bar(width: int, height: int, fraction: float):
    """The disposition meter filled to `fraction`, dimmed under its number."""
    panel = _outline(width, height, BOX_ALPHA, TRACK_RGBA)
    filled = max(0, min(width - 4, round((width - 4) * fraction)))
    if filled:
        fill = _shaded(filled, height - 4, DISPOSITION_RGB)
        fill.alpha_composite(Image.new('RGBA', fill.size, (0, 0, 0, 90)))
        panel.alpha_composite(fill, (2, 2))
    return panel


class SkyrimArt:
    """Skyrim's look, with `icons` from whichever game supplies them.

    The same members as `MorrowindArt`, so the generators build either.
    See: docs/commentary/morrowind_runtime.md#menu-styles
    """

    style = 'skyrim'
    colors = FONT_COLORS
    background = BACKGROUND
    cover = BACKGROUND

    def __init__(self, icons):
        self.icons = icons

    compose_frame = staticmethod(compose_frame)
    compose_box = staticmethod(compose_box)
    compose_head = staticmethod(compose_head)
    compose_button = staticmethod(compose_button)
    compose_scrollbar = staticmethod(compose_scrollbar)
    compose_thumb = staticmethod(compose_thumb)
    compose_line = staticmethod(compose_line)
    compose_cap = staticmethod(compose_cap)
    compose_stat_bar = staticmethod(compose_stat_bar)
    compose_bar = staticmethod(compose_bar)

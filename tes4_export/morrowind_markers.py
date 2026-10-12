"""Map markers synthesized from Morrowind cell data.

Morrowind has no map-marker object, so the authored substitute is a teleport
door -- where a builder decided a place is entered from the world -- named for
the interior cell behind it.

See: docs/commentary/tes4_export_morrowind.md#map-markers
"""

import re
from collections import defaultdict

#: TES4 marker icons; the importer maps these to TES5 icons.
ICON_CAMP = 1
ICON_CAVE = 2
ICON_CITY = 3
ICON_ELVEN_RUIN = 4
ICON_FORT_RUIN = 5
ICON_MINE = 6
ICON_LANDMARK = 7
ICON_TAVERN = 8
ICON_SETTLEMENT = 9
ICON_DAEDRIC_SHRINE = 10
ICON_STRONGHOLD = 11

#: World exits a place needs before its size alone names it.
CITY_DOORS = 20
SETTLEMENT_DOORS = 8

#: Dwemer syllable-initial clusters; bare "nch" is ordinary spelling.
DWEMER_CLUSTERS = ('mz', 'bth', 'rkng', 'zt', 'ngth', 'kng', 'zund', 'gnth',
                   'nchu', 'ncha', 'nche', 'nchi', 'ncho')

#: Commerce means a settlement, not a house.
TRADE_TOKENS = ('trader', 'clothier', 'pawnbroker', 'tradehouse', 'guild',
                'smith', 'alchemist', 'bookseller', 'apothecary', 'outfitter')

#: A public house; TES4 type 8 is the inn/stable icon, not a private home.
INN_TOKENS = ('inn', 'tavern', 'tradehouse', 'cornerclub', 'corner club',
              'stable', 'guildhall')

#: Undiscovered, as 377 of Skyrim's 397 markers ship; discovery reveals them.
MARKER_FLAGS = 0

_LETTERS = re.compile(r'[^a-z]')

#: Place/room separator: "Balmora, Council Club", "TEM Princess Morgiah: Hold".
_ROOM_SEPARATOR = re.compile(r'[,:]')


def place_name(cell_name: str) -> str:
    """The marker name for an interior cell: its text before the room separator.

    See: docs/commentary/tes4_export_morrowind.md#map-markers
    """
    head = _ROOM_SEPARATOR.split(cell_name, 1)[0].strip()
    return head or cell_name


def _is_dwemer(name: str, _text: str, _doors: int) -> bool:
    """Whether a place name reads as Dwemer by its consonant clusters."""
    flat = _LETTERS.sub('', name.lower())
    return any(cluster in flat for cluster in DWEMER_CLUSTERS)


def _tokens(*tokens):
    """A rule test matching any of `tokens` in the joined cell names."""
    return lambda _name, text, _doors: any(tok in text for tok in tokens)


def _bigger_than(limit):
    """A rule test matching a place with at least `limit` world exits."""
    return lambda _name, _text, doors: doors >= limit


#: Ordered (test, icon); see the module docs for why size precedes kind.
_RULES = (
    (_tokens('camp'), ICON_CAMP),
    (_bigger_than(CITY_DOORS), ICON_CITY),
    (_bigger_than(SETTLEMENT_DOORS), ICON_SETTLEMENT),
    (_tokens('propylon'), ICON_STRONGHOLD),
    (_tokens('tomb', 'barrow', 'shipwreck'), ICON_LANDMARK),
    (_tokens('mine'), ICON_MINE),
    (_tokens('shrine', 'temple'), ICON_DAEDRIC_SHRINE),
    (_is_dwemer, ICON_ELVEN_RUIN),
    (_tokens('grotto', 'cave', 'cavern'), ICON_CAVE),
    (_tokens(*TRADE_TOKENS), ICON_SETTLEMENT),
    (_tokens('tower', 'fort'), ICON_FORT_RUIN),
    (_tokens(*INN_TOKENS), ICON_TAVERN),
)


def classify(name: str, interiors: list, doors: int) -> int:
    """The marker icon for one place, by the first matching rule."""
    text = ' '.join([name] + interiors).lower()
    for test, icon in _RULES:
        if test(name, text, doors):
            return icon
    return ICON_LANDMARK


def collect_exits(cell) -> list:
    """Every world position an interior cell's teleport doors emerge at."""
    return [ref.dest_pos for ref in cell.refs
            if ref.teleport and ref.dest_pos and not ref.dest_cell
            and not ref.deleted]


def _editor_id(name: str) -> str:
    """A unique-enough EditorID for a marker; the CK renames collisions."""
    stem = ''.join(c for c in name if c.isalnum()) or 'Marker'
    return f'TES3{stem}MapMarker'


def marker_lines(name: str, icon: int, pos: tuple, parent_cell: str) -> list:
    """The export text for one map-marker REFR.

    `NAME=00000010` is the MapMarker static, shared by Oblivion and Skyrim.
    """
    return [f'EditorID={_editor_id(name)}',
            'RecordFlags=1024',
            f'ParentCELL={parent_cell}',
            'NAME=00000010',
            'MapMarker=1',
            f'MapMarker.Flags={MARKER_FLAGS}',
            f'MapMarker.FULL={name}',
            f'MapMarker.Type={icon}',
            f'PosX={pos[0]}', f'PosY={pos[1]}', f'PosZ={pos[2]}',
            'RotX=0.0', 'RotY=0.0', 'RotZ=0.0']


class MarkerBuilder:
    """Accumulates interior exits, then emits one marker per named place."""

    def __init__(self):
        """An empty builder; feed it interiors with `note_interior`."""
        self._exits = defaultdict(list)
        self._interiors = defaultdict(list)

    def note_interior(self, cell) -> None:
        """Record an interior cell's world exits under its place name."""
        exits = collect_exits(cell)
        if not exits:
            return
        place = place_name(cell.name)
        self._exits[place].extend(exits)
        self._interiors[place].append(cell.name)

    def markers(self) -> list:
        """One (name, icon, position) per place, sorted by name.

        The position averages the place's exits, so a town's marker sits in
        the town rather than at whichever entrance was read first.
        """
        out = []
        for place in sorted(self._exits):
            points = self._exits[place]
            icon = classify(place, self._interiors[place], len(points))
            x = sum(p[0] for p in points) / len(points)
            y = sum(p[1] for p in points) / len(points)
            z = max(p[2] for p in points)
            out.append((place, icon, (x, y, z)))
        return out

"""The default package list that leaves an actor standing where it is.

Every vanilla default hold-position list (DefaultHoldPositionCurrentLoc64List
and its siblings) is flagged Weapon Drawn -- Bethesda hands them to guards and
ambushers -- so an idle actor on one readies its weapon. This is
DefaultHoldPositionCurrentLoc64 without that flag: vanilla's HoldPosition
template at the actor's current location, radius 64.
"""

import struct

from .converter import ANY_TIME_PSDT, SPEED_WALK, Inputs, build_pkdt, package_markers
from .templates import HOLD_POSITION
from ..record_types.common import (pack_formid_subrecord, pack_record,
                                   pack_string_subrecord, pack_subrecord)

#: EditorIDs of the list and its one package; a dependent plugin adopts its master's list by the first.
STAND_STILL_LIST, _PACK_EDID = 'TES4StandStillList', 'TES4StandStill'

#: derive_formid sites.
_PACK_SITE, _LIST_SITE = 'STAND_STILL_PACK', 'STAND_STILL_FLST'

#: PLDT type 2, near the actor's current location, at DefaultHoldPositionCurrentLoc64's radius.
_CURRENT_LOCATION, _RADIUS = 2, 64


def _package(fid: int) -> bytes:
    """HoldPosition at the current location, with no flags."""
    inputs = Inputs(HOLD_POSITION)
    inputs.set('location', (_CURRENT_LOCATION, 0, _RADIUS))
    subs = pack_string_subrecord('EDID', _PACK_EDID)
    subs += pack_subrecord('PKDT', build_pkdt(0, SPEED_WALK))
    subs += pack_subrecord('PSDT', ANY_TIME_PSDT)
    subs += pack_subrecord('PKCU', struct.pack('<III', len(inputs.t.inputs), inputs.t.formid, inputs.t.version))
    subs += inputs.emit()
    subs += package_markers()
    return pack_record('PACK', fid, 0, subs)


def create_stand_still(writer, master_index=None) -> int:
    """The stand-still FLST, adopted from a master that has one, else written; its FormID."""
    fid = master_index.find_by_edid(b'FLST', STAND_STILL_LIST) if master_index is not None else 0
    if fid:
        return fid
    pack_fid = writer.derive_formid(_PACK_SITE, _PACK_EDID)
    writer.add_record('PACK', _package(pack_fid))
    fid = writer.derive_formid(_LIST_SITE, STAND_STILL_LIST)
    writer.add_record('FLST', pack_record('FLST', fid, 0, pack_string_subrecord('EDID', STAND_STILL_LIST)
                                          + pack_formid_subrecord('LNAM', pack_fid)))
    return fid

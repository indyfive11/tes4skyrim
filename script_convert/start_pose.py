"""The placed pose a TES4 script reads with GetStartingPos / GetStartingAngle.

TES4 answers both from the reference's record: where it was placed, constant
for its life.  Papyrus has no such read, so a script that asks takes its own
pose once, into variables saved with it, and every read goes through a getter
that takes the pose first if nothing has yet.

See: docs/commentary/script_convert.md#starting-pose
"""

#: TES4 command -> (getter stem, variable stem, the Papyrus read the variable is taken from).
_KINDS = {
    'getstartingpos': ('TES4_StartPos', 'TES4_Start', 'GetPosition'),
    'getstartingangle': ('TES4_StartAngle', 'TES4_StartAng', 'GetAngle'),
}

#: The function that takes the pose; the start events call it before anything else runs.
CAPTURE = 'TES4_CaptureStart'

#: True once the pose has been taken, so it is never taken again.
_FLAG = 'TES4_HaveStart'


def read(sc, command: str, axis: str) -> str:
    """The getter call that stands for one read; the axis is recorded on the script."""
    sc.start_pose.add((command, axis))
    return f'{_KINDS[command][0]}{axis}()'


def capture_call(sc) -> list:
    """The line a start event opens with, for a script that reads its placed pose."""
    return [f'  {CAPTURE}()'] if sc.start_pose else []


def open_load(sc, body: list) -> list:
    """`body` with the capture opening its `OnLoad`, the converter's or the one the script authored."""
    if 'Event OnLoad()' not in body:
        return body
    at = body.index('Event OnLoad()') + 1
    return body[:at] + capture_call(sc) + body[at:]


def helpers(sc, holder: str) -> list:
    """The variables, the capture function and one getter per axis the script reads.

    Sorted, so the text does not depend on the order the reads were met in.
    `holder` names the variable holding a carried object's container, or ''; a
    held object has no pose to take.
    """
    if not sc.start_pose:
        return []
    reads = sorted(sc.start_pose)
    held = f'{holder} != None || ' if holder else ''
    out = [f'Float {_KINDS[cmd][1]}{axis} = 0.0' for cmd, axis in reads]
    out += [f'Bool {_FLAG} = False', '', f'Function {CAPTURE}()',
            f'  If {_FLAG} || {held}GetParentCell() == None', '    Return', '  EndIf']
    out += [f'  {_KINDS[cmd][1]}{axis} = {_KINDS[cmd][2]}{axis}()' for cmd, axis in reads]
    out += [f'  {_FLAG} = True', 'EndFunction', '']
    for cmd, axis in reads:
        out += [f'Float Function {_KINDS[cmd][0]}{axis}()', f'  {CAPTURE}()',
                f'  Return {_KINDS[cmd][1]}{axis}', 'EndFunction', '']
    return out

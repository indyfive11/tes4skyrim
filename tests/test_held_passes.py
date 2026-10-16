"""A held carriable item keeps polling from its own events when its poll body calls nothing on Self.

TES4 ran a carried item's GameMode block from the holder's inventory.  A held
carriable item has no native object, so its poll stopped at pickup: a journal
whose four-second countdown started when it was taken never finished, and the
gate it opened stayed shut.  A body that acts on Self keeps the old guard.

See: docs/commentary/script_convert.md#held-items-keep-polling
"""
from types import SimpleNamespace

import pytest

from script_convert.assemble import _acts_on_self, _calls_nothing_on_self, _self_holders
from script_convert.converter import ScriptConverter
from script_convert.cross_ref import CrossRefGraph

GATE = 'TES4_Holder == None && TES4Polyfill.SafeGameModeGate(Self)'


def _convert(source: str, book: bool = False, carriable: bool = True, consumable: bool = False) -> str:
    """One standalone conversion of an ObjectReference script on carriable bases."""
    conv = ScriptConverter(CrossRefGraph())
    conv.sc.carriable_only = carriable
    conv.sc.on_book = book
    conv.sc.consumable = consumable
    return conv.convert_standalone('ItemScript', source, 'ObjectReference', 'ItemScript')


def _event(result: str, header: str) -> str:
    """The body of the event that opens with `header`."""
    return result.split(header, 1)[1].split('EndEvent', 1)[0]


COUNTDOWN = ('ScriptName ItemScript\nshort init\nfloat timer\nref gate\n'
             'Begin GameMode\n  if init == 1\n    set timer to timer - getSecondsPassed\n'
             '    if timer <= 0\n      gate.activate player 1\n      set init to 2\n'
             '    endif\n  endif\nEnd\n')


class TestHeldLoop:

    def test_a_body_that_never_touches_self_keeps_polling_while_held(self):
        """The holder events start the loop, and the loop runs the poll through OnUpdate."""
        result = _convert(COUNTDOWN)
        loop = result.split('Function TES4_HeldPasses()', 1)[1].split('EndFunction', 1)[0]
        assert 'OnUpdate()' in loop and 'Utility.Wait(' in loop
        assert 'TES4Polyfill.SafeGameModeGate(TES4_Holder)' in loop
        assert '  TES4_HeldPassRun += 1' in loop
        assert 'While TES4_mine == TES4_HeldPassRun && ' in loop
        assert 'Self' not in loop
        for header in ('Event OnContainerChanged(', 'Event OnEquipped('):
            body = _event(result, header)
            assert body.rstrip().endswith('TES4_HeldPasses()')
            assert 'RegisterForSingleUpdate' not in body

    def test_its_poll_has_no_held_guard(self):
        """The guard would end every pass the loop starts."""
        update = _event(_convert(COUNTDOWN), 'Event OnUpdate()')
        assert 'If TES4_Holder != None' not in update
        assert GATE in update

    def test_a_body_that_acts_on_self_keeps_the_guard_and_gets_no_loop(self):
        """A held item has no native object, so that body would fail on every pass."""
        result = _convert('ScriptName ItemScript\nBegin GameMode\n  disable\nEnd\n')
        assert 'TES4_HeldPasses' not in result
        assert _event(result, 'Event OnUpdate()').lstrip().startswith('If TES4_Holder != None')

    def test_a_world_object_on_the_broad_path_gets_no_loop(self):
        """It re-arms from the holder events; its Self stays bound."""
        result = _convert(COUNTDOWN, carriable=False)
        assert 'TES4_HeldPasses' not in result
        assert 'SafeGameModeGate(TES4_Holder) ||' in result

    def test_a_return_inside_the_body_does_not_count_as_acting_on_self(self):
        """A converted return re-arms behind the poll's own gate, which tests the holder first."""
        source = ('ScriptName ItemScript\nshort x\nBegin GameMode\n  if x == 1\n    return\n'
                  '  endif\n  set x to 1\nEnd\n')
        result = _convert(source)
        assert GATE in _event(result, 'Event OnUpdate()')
        assert 'Function TES4_HeldPasses()' in result

    def test_a_read_from_the_inventory_does_not_start_the_loop(self):
        """OnRead sets the holder to the player even for a book read from a chest; the pickup starts it."""
        source = ('ScriptName NoteScript\nshort lesen\nBegin OnActivate\n  set lesen to 1\n'
                  '  Activate\nEnd\nBegin GameMode\n  if lesen == 1\n    set lesen to 2\n'
                  '  endif\nEnd\n')
        result = _convert(source, book=True)
        assert 'OnUpdate()' in _event(result, 'Event OnRead()')
        assert 'TES4_HeldPasses' not in _event(result, 'Event OnRead()')
        assert _event(result, 'Event OnContainerChanged(').rstrip().endswith('TES4_HeldPasses()')

    def test_an_item_the_game_uses_up_gets_no_loop(self):
        """No holder event follows eating or spending it, so the loop would never end."""
        assert 'TES4_HeldPasses' not in _convert(COUNTDOWN, consumable=True)

    def test_a_holder_event_that_can_return_gets_no_loop(self):
        """The converted Return would end the event before the loop call."""
        source = ('ScriptName ItemScript\nshort done\nfloat timer\nBegin OnAdd player\n'
                  '  if done == 1\n    return\n  endif\n  set done to 1\n  set timer to 4\nEnd\n'
                  'Begin GameMode\n  set timer to timer - getSecondsPassed\nEnd\n')
        assert 'TES4_HeldPasses' not in _convert(source)

    def test_a_variable_set_to_getself_counts_as_self(self):
        """`me.Disable()` acts on the held item as surely as `Disable()`."""
        source = ('ScriptName ItemScript\nref me\nBegin OnAdd player\n  set me to getself\nEnd\n'
                  'Begin GameMode\n  me.disable\nEnd\n')
        result = _convert(source)
        assert 'TES4_HeldPasses' not in result
        assert 'If TES4_Holder != None' in _event(result, 'Event OnUpdate()')


class TestSelfHolders:

    def test_a_property_typed_as_the_script_itself(self):
        """The item's own persistent ref, as Mother's Head's script names it."""
        conv = SimpleNamespace(sc=SimpleNamespace(
            edid='MothersHeadScript', var_types={},
            property_refs={'MothersHeadRef': 'TES4_MothersHeadScript', 'MathieuRef': 'Actor'}))
        holders = _self_holders(conv, None)
        assert holders == {'mothersheadref'}
        assert not _calls_nothing_on_self(['  If MothersHeadRef.GetDistance(MathieuRef) <= 400'],
                                          GATE, holders)
        assert _calls_nothing_on_self(['  If MathieuRef.GetDistance(Bob) <= 400'], GATE, holders)


class TestActsOnSelf:

    @pytest.mark.parametrize('line', [
        '  Self.MoveTo(x)', '  Disable()', '  If GetDistance(Player) < 5',
        '  TES4Polyfill.IsInContainer(Self)', '  Float d = GetPositionX()'])
    def test_a_call_on_self(self, line):
        """Named Self, or a call with no receiver."""
        assert _acts_on_self(line, GATE)

    @pytest.mark.parametrize('line', [
        '  mySelf = akActionRef', '  Game.GetPlayer().EquipItem(Sword)',
        '  If (x == 1) && (y as Int) > 0', '  Debug.Notification("Self(test)")',
        '  ; Disable() was here', '  ElseIf (timer <= 0)', '  Return',
        f'  If ({GATE})'])
    def test_not_a_call_on_self(self, line):
        """A receiver, a keyword, a string, a comment, a longer name, the poll's own gate."""
        assert not _acts_on_self(line, GATE)

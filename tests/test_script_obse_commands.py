"""TES4 blocks and commands that previously dropped, no-opped or failed to compile."""

from script_convert.converter import ScriptConverter
from script_convert.cross_ref import CrossRefGraph

_HEAD = 'Scriptname T\nshort n\nref item\nref actr\nbegin {blk}\n{body}\nend\n'


def _convert(body: str, extends: str = 'Quest', blk: str = 'GameMode',
             xref: CrossRefGraph = None) -> str:
    """Convert one TES4 block body inside a throwaway script."""
    src = _HEAD.format(blk=blk, body=body)
    return ScriptConverter(xref or CrossRefGraph()).convert_standalone(
        'T', src, extends, 'T')


def test_onknockout_becomes_enter_bleedout():
    """OnKnockout's body survives as OnEnterBleedout instead of being dropped."""
    out = _convert('set n to 1', 'Actor', 'OnKnockout')
    assert 'Event OnEnterBleedout()' in out
    assert out.split('Event OnEnterBleedout()', 1)[1].lstrip().startswith('n = 1')


def test_isactor_is_a_cast_test():
    """IsActor on a ref and bare both test the Actor cast."""
    assert 'n = ((item as Actor) != None) as Int' in _convert('set n to item.IsActor')
    bare = _convert('if IsActor\nset n to 1\nendif', 'ObjectReference', 'OnLoad')
    assert '(Self as Actor) != None' in bare


def test_actor_ai_commands_drive_enable_ai():
    """SetActorsAI/ToggleActorsAI/IsActorsAIOff use EnableAI and IsAIEnabled."""
    assert 'actr.EnableAI(false)' in _convert('actr.SetActorsAI 0')
    assert 'actr.EnableAI(!actr.IsAIEnabled())' in _convert('actr.ToggleActorsAI')
    assert 'If (!actr.IsAIEnabled())' in _convert(
        'if actr.IsActorsAIOff\nset n to 1\nendif')


def test_isplayable_reads_the_base_form():
    """IsPlayable2 <obj> and a bare IsPlayable go through the polyfill."""
    assert 'n = TES4Polyfill.IsPlayable(item) as Int' in _convert(
        'set n to IsPlayable2 item')
    bare = _convert('if IsPlayable == 1\nset n to 1\nendif', 'ObjectReference', 'OnLoad')
    assert 'If TES4Polyfill.IsPlayable(Self)' in bare


def test_effect_subject_is_the_target_actor():
    """In a magic effect, Self is the effect: SetDestroyed and PlayGroup act on the target."""
    out = _convert('SetDestroyed 1\nPlayGroup Idle 1', 'ActiveMagicEffect',
                   'ScriptEffectStart')
    assert 'TES4Polyfill.SetDestroyed(GetTargetActor(), TES4DestroyedRefs, true)' in out
    assert 'Debug.SendAnimationEvent(GetTargetActor(), ' in out
    assert 'PlayAnimation' not in out


def test_bool_plus_bool_casts_each_operand():
    """Papyrus rejects `Bool + Bool`; a Bool beside a number needs no cast."""
    both = _convert('set n to item.GetDisabled + actr.GetDisabled')
    assert '(TES4Polyfill.GetDisabled(item, TES4DestroyedRefs) as Int) + ' in both
    one = _convert('set n to player.IsInCombat - 1')
    assert 'n = Game.GetPlayer().IsInCombat() - 1' in one


def _leading_digit_xref() -> CrossRefGraph:
    """A quest `1FlightQuest` whose script declares only `Summoned`."""
    x = CrossRefGraph()
    x.edid_to_formid['1flightquest'] = '01000003'
    x.formid_to_edid['01000003'] = '1FlightQuest'
    x.record_type['01000003'] = 'QUST'
    x.record_scri['01000003'] = '01000001'
    x.script_formid_to_edid['01000001'] = '1FlightScript'
    x.script_all_vars['1flightscript'] = {'summoned': 'Int'}
    return x


def test_leading_digit_owner_takes_its_script_class():
    """A stripped leading-digit owner is typed as its script and dangling fields are neutralised."""
    out = _convert('set FlightQuest.Summoned to 0\nif FlightQuest.Other == 1\n'
                   'set FlightQuest.Other to 0\nendif', xref=_leading_digit_xref())
    assert 'TES4_1FlightScript Property d1FlightQuest Auto' in out
    assert 'd1FlightQuest.Summoned = 0' in out
    assert 'If false' in out
    assert ';d1FlightQuest.Other = 0' in out

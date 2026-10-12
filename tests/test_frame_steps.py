"""A fixed per-frame step of a float variable that is seen in game is scaled by elapsed time.

See: docs/commentary/script_convert.md#variable-steps-are-rates
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from script_convert import frame_steps as F
from script_convert.converter import ScriptConverter
from script_convert.cross_ref import CrossRefGraph
from script_convert.tes4.parser import parse

RATE = ' * 30.0 * TES4_SecondsPassed'


def _script(*body, decl=('float alpha', 'float h', 'float z', 'short n', 'short stage'),
            block='GameMode', other=()):
    """Source of a script: one block holding `body`, then `other` verbatim."""
    return '\n'.join(('scn Probe', '') + tuple(decl) + ('', f'begin {block}')
                     + tuple(body) + ('end', '') + tuple(other) + ('',))


def _steps(*body, **kw):
    """{variable: sign} of the steps the rule would scale in `body`."""
    found = F.frame_steps(parse(_script(*body, **kw)))
    return {name: sign for (_, name), (_, sign) in found.items()}


def _scaled(*body, **kw):
    """The literals of the steps the rule would scale, in source order."""
    found = F.frame_steps(parse(_script(*body, **kw)))
    return [literal.text for _, (literal, _) in sorted(found.items())]


def _convert(*body, extends='ObjectReference', **kw):
    """The converted Papyrus of a script holding `body`."""
    conv = ScriptConverter(CrossRefGraph())
    return conv.convert_standalone('Probe', _script(*body, **kw), extends, 'Probe')


class TestWhichStepsAreRates:
    """What `frame_steps` picks out of a poll block."""

    def test_a_fade_step_is_a_rate(self):
        assert _steps('set alpha to alpha - 0.01', 'SetActorAlpha alpha') == {'alpha': '-'}

    def test_the_short_command_name_counts_too(self):
        assert _steps('set alpha to alpha + 0.01', 'saa alpha') == {'alpha': '+'}

    @pytest.mark.parametrize('sink', [
        'SetPos z h', 'SetAngle x h', 'SetScale h', 'Rotate z h',
        'MoveTo player 0 0 h', 'PositionWorld 0 0 h 0 Tamriel',
        'PositionCell 0 0 h 0 SomeCell',
    ])
    def test_every_seen_command_makes_its_argument_a_rate(self, sink):
        assert _steps('set h to h + 0.1', sink) == {'h': '+'}

    def test_a_variable_that_feeds_a_seen_one_is_a_rate(self):
        """The orbit radius reaches SetPos through two other variables."""
        assert _steps('set h to h - 0.1', 'set alpha to h * 2',
                      'set z to 100 + alpha', 'SetPos z z') == {'h': '-'}

    @pytest.mark.parametrize('step, sign', [
        ('let h += 0.1', '+'), ('let h -= 0.1', '-'),
        ('set h to 0.1 + h', '+'), ('set h to ( h - .1 )', '-'),
        ('set h to h + -0.1', '-'), ('set h to h - -0.1', '+'),
    ])
    def test_every_spelling_of_a_step(self, step, sign):
        assert _steps(step, 'SetScale h') == {'h': sign}

    def test_the_literal_handed_back_is_unsigned(self):
        """The sign travels apart from the number, so `+ -0.25` emits as `- 0.25`."""
        found = F.frame_steps(parse(_script('set h to h + -0.25', 'SetScale h')))
        (literal, sign), = found.values()
        assert (literal.text, sign) == ('0.25', '-')

    def test_a_variable_that_reaches_nothing_seen_is_left(self):
        assert _steps('set h to h + 4', 'if h > 20', 'set stage to 1', 'endif') == {}

    def test_a_whole_number_counter_is_left(self):
        assert _steps('set n to n + 1', 'SetScale n') == {}

    @pytest.mark.parametrize('test', ['if h == 2', 'if h != 2', 'if 2 == h'])
    def test_a_variable_compared_exactly_is_left(self, test):
        assert _steps('set h to h + 0.1', 'SetScale h', test, 'set stage to 1', 'endif') == {}

    def test_an_axis_name_is_not_a_value(self):
        """`SetPos z h` moves along z; a variable called z is not what is set."""
        assert _steps('set z to z + 0.1', 'SetPos z h') == {}

    def test_a_step_that_is_not_a_step_is_left(self):
        assert _steps('set h to h * 1.01', 'set alpha to alpha + h',
                      'set z to z + 0', 'SetScale h', 'SetScale alpha', 'SetScale z') == {}

    def test_a_branch_that_switches_itself_off_is_a_one_off(self):
        assert _steps('if stage == 1', 'set h to h + 50', 'SetPos z h',
                      'set stage to 2', 'endif') == {}

    def test_a_switch_nested_deeper_does_not_make_a_one_off(self):
        """Jyggalag's fade: the stage ends in another branch, the step runs every pass."""
        assert _steps('if stage == 2', 'if h > 0', 'set alpha to alpha - 0.01',
                      'SetActorAlpha alpha', 'else', 'set stage to 3', 'endif',
                      'endif') == {'alpha': '-'}

    @pytest.mark.parametrize('switch', [
        'set stage to n',            # re-armed from a variable
        'set stage to 1 - stage',    # toggled
        'set stage to GetRandomPercent',
    ])
    def test_any_new_value_for_a_tested_variable_switches_the_branch_off(self, switch):
        assert _steps('if stage == 2', 'set h to h + 50', 'SetScale h', switch, 'endif') == {}

    @pytest.mark.parametrize('drain', [
        'set alpha to alpha - GetSecondsPassed', 'let alpha -= n',
    ])
    def test_a_tested_variable_that_is_drained_keeps_the_branch_running(self, drain):
        """Jyggalag's fade runs while its timer drains; the timer is no switch."""
        assert _steps('if alpha > 0', drain, 'set h to h + 0.1', 'SetScale h',
                      'endif', decl=('float alpha', 'float h', 'short n')) == {'h': '+'}

    @pytest.mark.parametrize('flag', ['SomeQuest.go', 'SomeRef.go'])
    def test_a_flag_kept_on_a_quest_or_a_reference_switches_off_too(self, flag):
        assert _steps(f'if {flag} == 1', 'set h to h + 50', 'SetScale h',
                      f'set {flag} to 0', 'endif') == {}

    def test_another_member_of_the_same_owner_is_no_switch(self):
        assert _steps('if SomeQuest.go == 1', 'set h to h + 0.1', 'SetScale h',
                      'set SomeQuest.count to 0', 'endif') == {'h': '+'}

    def test_a_return_guard_governs_what_follows_it(self):
        """`if done / return / endif` then a step and `set done to 1`: a do-once."""
        assert _steps('if stage == 1', 'return', 'endif', 'set h to h + 50',
                      'SetScale h', 'set stage to 1') == {}

    def test_a_return_guard_alone_does_not_stop_a_rate(self):
        assert _steps('if stage == 1', 'return', 'endif', 'set h to h + 0.1',
                      'SetScale h') == {'h': '+'}

    def test_a_guard_that_does_more_than_return_is_no_return_guard(self):
        assert _steps('if stage == 1', 'set n to 0', 'return', 'endif',
                      'set h to h + 0.1', 'SetScale h', 'set stage to 1') == {'h': '+'}

    def test_a_switch_in_every_branch_one_deeper_is_a_switch(self):
        assert _steps('if stage == 1', 'set h to h + 50', 'SetScale h', 'if n > 2',
                      'set stage to 2', 'else', 'set stage to 3', 'endif', 'endif') == {}

    def test_a_switch_in_only_some_branches_one_deeper_is_none(self):
        assert _steps('if stage == 1', 'set h to h + 0.1', 'SetScale h', 'if n > 2',
                      'set stage to 2', 'else', 'set n to 0', 'endif', 'endif') == {'h': '+'}

    def test_a_stage_set_on_another_quest_is_no_switch(self):
        assert _steps('if GetStage SomeQuest == 10', 'set h to h + 0.1', 'SetScale h',
                      'SetStage OtherQuest 20', 'endif') == {'h': '+'}

    def test_a_one_off_one_level_out_still_counts(self):
        """The step sits in an inner branch; the outer one switches itself off."""
        assert _steps('if stage == 1', 'if n > 2', 'set h to h + 50', 'SetScale h',
                      'endif', 'set stage to 2', 'endif') == {}

    def test_a_pass_counter_one_level_out_still_counts(self):
        assert _steps('if n < 100', 'if stage == 1', 'set alpha to alpha - 0.01',
                      'SetActorAlpha alpha', 'endif', 'set n to n + 1', 'endif') == {}

    def test_a_jump_under_an_outer_test_of_its_variable_still_counts(self):
        assert _scaled('set h to h + 2', 'if h > 360', 'if stage == 1',
                       'set h to h - 360', 'endif', 'endif', 'SetAngle z h') == ['2']

    def test_a_stage_call_switches_a_branch_off_too(self):
        assert _steps('if GetStage SomeQuest == 10', 'set h to h + 50', 'SetPos z h',
                      'SetStage SomeQuest 20', 'endif') == {}

    def test_an_else_branch_is_governed_by_the_test_before_it(self):
        assert _steps('if stage != 1', 'set n to 0', 'else', 'set h to h + 50',
                      'SetScale h', 'set stage to 2', 'endif') == {}

    def test_an_else_branch_that_keeps_running_is_a_rate(self):
        assert _steps('if stage == 1', 'set n to 0', 'else', 'set h to h + 0.1',
                      'SetScale h', 'endif') == {'h': '+'}

    @pytest.mark.parametrize('test, jump', [
        ('if h > 360', 'set h to h - 360'),      # as large as its limit
        ('if h >= 360', 'set h to h - 360'),
        ('if h > 180', 'set h to h - 360'),
        ('if 6.2832 < h', 'set h to h - 6.2832'),
        ('if h < 100', 'set h to h + 500'),
        ('if h <= 100', 'set h to h + 500'),
        ('if h > 100', 'set h to h - 100'),      # equal to its limit, no turn
        ('if h < 0', 'set h to h + 360'),        # zero limit: a turn
        ('if h < 0', 'set h to h + 180'),
        ('if h < 0', 'set h to h + 6.2832'),
        ('if h < 0', 'set h to h + 3.1416'),
        ('if h - 360 > 0', 'set h to h - 360'),  # the limit inside an expression
        ('if h > n', 'set h to h - 360'),        # a limit that is a variable
    ])
    def test_a_step_as_large_as_its_limit_or_a_turn_is_a_jump(self, test, jump):
        """Only the two-degree turn per frame is the rate."""
        assert _scaled('set h to h + 2', test, jump, 'endif', 'SetAngle z h') == ['2']

    def test_a_step_across_the_whole_tested_range_is_a_jump(self):
        """A custom wrap: 0 to 100, with no turn in it and a zero limit on one side."""
        assert _scaled('set h to h + 1', 'if h > 100', 'set h to h - 100', 'endif',
                       'if h < 0', 'set h to h + 100', 'endif', 'SetScale h') == ['1']

    def test_a_turn_sized_step_under_no_test_of_its_variable_is_a_rate(self):
        assert _scaled('set h to h + 180', 'SetAngle z h') == ['180']

    def test_a_step_near_a_turn_is_not_a_turn(self):
        """Half a degree off a whole turn, under a zero limit: nothing marks it a jump."""
        assert _scaled('set h to h + 2', 'if h < 0', 'set h to h + 359.5', 'endif',
                       'SetAngle z h') == ['2', '359.5']

    @pytest.mark.parametrize('far_test', [
        ('elseif h < 50', 'set n to 1', 'endif'),
        ('endif', 'while h < 50', 'set n to 0', 'loop'),
    ])
    def test_the_tested_range_counts_limits_in_every_kind_of_condition(self, far_test):
        """50 to 100: a step back of 60 is under its own limit but wider than the range."""
        head = ('set h to h + 1', 'if h > 100', 'set h to h - 60')
        assert _scaled(*head, 'endif', 'SetScale h') == ['1', '60']
        assert _scaled(*head, *far_test, 'SetScale h') == ['1']

    def test_a_reflection_is_not_a_step(self):
        assert _steps('set h to 1 - h', 'SetScale h') == {}

    def test_an_exact_test_in_another_block_protects_too(self):
        assert _steps('set h to h + 0.1', 'SetScale h', other=(
            'begin OnLoad', 'if h == 0', 'set stage to 1', 'endif', 'end')) == {}

    def test_a_seen_command_in_another_block_counts_too(self):
        """The size is applied on activation; the variable still ends in it."""
        assert _steps('set h to h + 0.1', other=(
            'begin OnActivate', 'SetScale h', 'end')) == {'h': '+'}

    def test_a_variable_stepped_only_outside_the_poll_counts_no_passes(self):
        assert _steps('if n < 100', 'set alpha to alpha - 0.01', 'SetActorAlpha alpha',
                      'endif', other=('begin OnActivate', 'set n to n + 1', 'end')
                      ) == {'alpha': '-'}

    def test_a_step_toward_a_far_limit_is_a_rate(self):
        assert _steps('if h < 300', 'set h to h + 0.1', 'endif', 'SetScale h') == {'h': '+'}

    def test_a_limit_of_zero_is_no_jump(self):
        assert _steps('if alpha > 0', 'set alpha to alpha - 0.01', 'endif',
                      'SetActorAlpha alpha') == {'alpha': '-'}

    def test_a_branch_that_counts_its_own_passes_is_not_a_rate(self):
        """A hundred frames of fading: the step is a share of a total."""
        assert _steps('if n < 100', 'set alpha to alpha - 0.01', 'SetActorAlpha alpha',
                      'set n to n + 1', 'endif') == {}

    def test_a_counter_stepped_elsewhere_counts_too(self):
        assert _steps('set n to n + 1', 'if n < 100', 'set alpha to alpha - 0.01',
                      'SetActorAlpha alpha', 'endif') == {}

    def test_a_decimal_counter_counts_too(self):
        """`h` steps every pass and reaches nothing seen: it counts passes."""
        assert _steps('let h += 1', 'if h < 100', 'set alpha to alpha - 0.01',
                      'SetActorAlpha alpha', 'endif') == {}

    def test_a_timer_drained_by_elapsed_time_does_not_count_passes(self):
        assert _steps('if h > 0', 'set h to h - GetSecondsPassed',
                      'set alpha to alpha - 0.01', 'SetActorAlpha alpha',
                      'endif') == {'alpha': '-'}

    def test_a_test_of_another_scaled_variable_does_not_count_passes(self):
        """Both steps are rates, so the limit is reached at the same real time."""
        assert _steps('set h to h + 0.1', 'SetScale h', 'if h < 3',
                      'set alpha to alpha - 0.01', 'SetActorAlpha alpha',
                      'endif') == {'h': '+', 'alpha': '-'}

    def test_a_step_inside_a_loop_is_left(self):
        """It runs several times a pass, whatever the loop tests."""
        assert _steps('while stage == 1', 'set h to h + 0.1', 'loop', 'SetScale h') == {}
        assert _steps('while n < 3', 'set h to h + 0.1', 'let n += 1', 'loop',
                      'SetScale h') == {}

    def test_an_elseif_branch_is_governed_by_the_tests_before_it(self):
        """The first test named `stage`; the second branch ends it."""
        assert _steps('if stage == 1', 'set alpha to 1', 'elseif alpha > 3',
                      'set h to h + 50', 'SetScale h', 'set stage to 2', 'endif') == {}

    @pytest.mark.parametrize('block', ['OnActivate', 'OnLoad', 'MenuMode'])
    def test_a_block_that_does_not_run_every_frame_is_left(self, block):
        assert _steps('set h to h + 0.1', 'SetScale h', block=block) == {}

    def test_an_effect_s_update_block_is_a_poll(self):
        assert _steps('set h to h + 0.1', 'SetScale h', block='ScriptEffectUpdate') == {'h': '+'}

    def test_no_tree_no_steps(self):
        assert F.frame_steps(None) == {}

    def test_a_quest_script_has_none(self):
        """TES4 ran a quest script every fQuestDelayTime, not every frame."""
        tree = parse(_script('set h to h + 0.1', 'SetScale h'))
        assert F.frame_steps(tree, 'Quest') == {}
        assert len(F.frame_steps(tree, 'ObjectReference')) == 1


class TestScaledStepsAreEmitted:
    """What the converter writes for a scaled step, and what it leaves as it was."""

    def test_the_step_is_multiplied_by_frames_passed(self):
        out = _convert('set alpha to ( alpha - .01 )', 'SetActorAlpha alpha', extends='Actor')
        assert f'alpha = alpha - 0.01{RATE}' in out

    def test_a_compound_step_is_multiplied_too(self):
        out = _convert('let h += 0.5', 'SetScale h')
        assert f'h = h + 0.5{RATE}' in out

    def test_a_negative_literal_folds_into_the_sign(self):
        out = _convert('set h to h + -0.5', 'SetScale h')
        assert f'h = h - 0.5{RATE}' in out

    def test_the_script_measures_the_pass_and_polls_fast(self):
        """Without GetSecondsPassed or a SetPos the script had neither."""
        out = _convert('set alpha to alpha - 0.01', 'SetActorAlpha alpha', extends='Actor')
        assert 'Float TES4_SecondsPassed = 0.1' in out
        assert 'TES4_SecondsPassed = TES4_Now - TES4_LastTick' in out
        assert 'RegisterForSingleUpdate(0.1)' in out
        body = out.split('Event OnUpdate()', 1)[1]
        assert body.index('TES4_LastTick = TES4_Now') < body.index(f'alpha - 0.01{RATE}')

    def test_a_script_without_a_scaled_step_gains_nothing(self):
        """No measurement, no faster poll, the step as written."""
        out = _convert('set h to h + 4', 'if h > 20', 'set stage to 1', 'endif')
        assert 'TES4_SecondsPassed' not in out
        assert 'h = h + 4\n' in out

    def test_the_other_assignments_of_the_variable_are_untouched(self):
        """Only the step is a rate; `set alpha to 0.9` stays a plain write."""
        out = _convert('if stage == 1', 'set alpha to 0.9', 'set stage to 2', 'endif',
                       'set alpha to alpha - 0.01', 'SetActorAlpha alpha', extends='Actor')
        assert 'alpha = 0.9\n' in out
        assert out.count(RATE) == 1

    def test_a_one_off_and_a_jump_keep_their_steps(self):
        """Three steps in one script: a one-off, a wrap-around and the one rate."""
        out = _convert('if stage == 1', 'set h to h + 50', 'set stage to 2', 'endif',
                       'if z > 360', 'set z to z - 360', 'endif',
                       'set z to z + 2', 'SetAngle z z', 'SetScale h')
        assert 'h = h + 50\n' in out
        assert 'z = z - 360\n' in out
        assert f'z = z + 2{RATE}' in out

    def test_a_quest_script_keeps_its_steps(self):
        """TES4 ran a quest script every fQuestDelayTime, not every frame."""
        out = _convert('set alpha to alpha - 0.01', 'player.SetActorAlpha alpha',
                       extends='Quest')
        assert RATE not in out
        assert 'alpha = alpha - 0.01\n' in out

    def test_an_effect_script_is_scaled(self):
        out = _convert('set h to h + 0.35', 'SetPos z h', block='ScriptEffectUpdate',
                       extends='ActiveMagicEffect')
        assert f'h = h + 0.35{RATE}' in out

    def test_a_timer_in_the_same_script_still_drains_in_real_time(self):
        """One measurement serves the timer and the scaled step."""
        out = _convert('set h to h - GetSecondsPassed', 'set alpha to alpha - 0.01',
                       'SetActorAlpha alpha', extends='Actor')
        assert 'h = h - TES4_SecondsPassed' in out
        assert out.count('Float TES4_SecondsPassed') == 1

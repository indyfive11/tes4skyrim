"""A quest whose stages are called as functions.

See: docs/commentary/script_convert.md#stage-function-libraries
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from script_convert.cross_ref import CrossRefGraph
from script_convert.pipeline import convert_all_scripts
from script_convert.stage_library import TURN_TRIES, library_functions

LIB_FID = '00001000'


def _rec(sig, fid, edid, **fields):
    """One export record as text."""
    extra = ''.join(f'{k}={v}\n' for k, v in fields.items())
    return (f'---RECORD_BEGIN---\nSignature={sig}\nFormID={fid}\n'
            f'EditorID={edid}\n{extra}---RECORD_END---\n')


def _src(*lines):
    """Source lines joined the way the export escapes them."""
    return '\\r\\n'.join(lines)


def _stage(i, index, script, **extra):
    """The fields of stage slot `i`: one log entry with `script` as its result."""
    fields = {f'Stage[{i}].Index': index, f'Stage[{i}].LogCount': 1,
              f'Stage[{i}].Log[0].Flags': 0,
              f'Stage[{i}].Log[0].ResultScript': script}
    fields.update({f'Stage[{i}].Log[0].{k}': v for k, v in extra.items()})
    return fields


def _library(flags=9, scri='00002000', stages=None, edid='Lib', fid=LIB_FID):
    """A QUST record: stage 5 doubles `in1` into `out`, stage 10 calls stage 5."""
    if stages is None:
        stages = [_stage(0, 5, _src('set Lib.out to Lib.in1 * 2')),
                  _stage(1, 10, _src('setstage Lib 5', 'set Lib.out to Lib.out + 1'))]
    fields = {'DATA.Flags': flags, 'StageCount': len(stages)}
    if scri:
        fields['SCRI'] = scri
    for stage in stages:
        fields.update(stage)
    return _rec('QUST', fid, edid, **fields)


def _script(fid, edid, kind, *lines):
    """A SCPT record holding `lines` as the body of script `edid`."""
    return _rec('SCPT', fid, edid, **{'SCHR.Type': kind,
                                     'SCTX': _src(f'scn {edid}', *lines)})


LIB_SCRIPT = _script('00002000', 'LibScript', 1, 'float in1', 'float out',
                     'short which', 'begin GameMode', 'end')

CALLER = _script('00003000', 'CallerScript', 0, 'float x', 'begin GameMode',
                 '  set Lib.in1 to x', '  setstage Lib 10', '  set x to Lib.out',
                 'end')


def _export(root, quests, scripts, name='Plugin.esp', masters=(), other=None):
    """Write a plugin export folder under `root` and return it."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / 'QUST.txt').write_text(''.join(quests), encoding='utf-8')
    (d / 'SCPT.txt').write_text(''.join(scripts), encoding='utf-8')
    header = ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters))
    (d / '_HEADER.txt').write_text(header, encoding='utf-8')
    for fname, text in (other or {}).items():
        (d / fname).write_text(text, encoding='utf-8')
    return d


def _graph(export_dir):
    """The graph the script stage builds for `export_dir`."""
    xref = CrossRefGraph()
    xref.load_from_export(str(export_dir))
    xref.build_script_indexes(str(export_dir / 'SCPT.txt'))
    return xref


def _stages(tmp_path, quests, scripts=(LIB_SCRIPT, CALLER), **kw):
    """The library stages the graph reports for quest `Lib`."""
    return _graph(_export(tmp_path, quests, scripts, **kw)).library_stages('Lib')


def _condition(function, quest_fid):
    """An INFO record with one condition calling `function` on `quest_fid`."""
    raw = struct.pack('<B3xfIII4x', 0, 5.0, function, int(quest_fid, 16), 0)
    return _rec('INFO', '00009000', '', ConditionCount=1,
                **{'Condition[0].Raw': raw.hex()})


class TestWhichQuestsQualify:
    def test_a_quest_whose_stage_calls_another_is_a_library(self, tmp_path):
        """Stage 10 sets stage 5: the quest is a library with both stages."""
        assert _stages(tmp_path, [_library()]) == (5, 10)

    def test_a_nested_call_is_enough_when_no_caller_reads_back(self, tmp_path):
        """A stage that sets another qualifies with no read-back anywhere."""
        stages = [_stage(0, 5, 'set Lib.out to 1'),
                  _stage(1, 10, _src('setstage Lib 5', 'set Lib.in1 to 0'))]
        lone = _script('00003000', 'CallerScript', 0, 'begin OnActivate',
                       '  setstage Lib 10', 'end')
        assert _stages(tmp_path, [_library(stages=stages)],
                       [LIB_SCRIPT, lone]) == (5, 10)

    def test_a_commented_out_nested_call_is_not_one(self, tmp_path):
        """A stage call behind a `;` is no call."""
        stages = [_stage(0, 5, 'set Lib.out to 1'),
                  _stage(1, 10, _src(';setstage Lib 5', '  ; setstage Lib 5',
                                     'set Lib.out to 2'))]
        lone = _script('00003000', 'CallerScript', 0, 'begin OnActivate',
                       '  setstage Lib 10', 'end')
        assert _stages(tmp_path, [_library(stages=stages)],
                       [LIB_SCRIPT, lone]) == ()

    def test_the_name_is_matched_whatever_its_case_or_quotes(self, tmp_path):
        """The lookup ignores case and quotes and knows no other name."""
        xref = _graph(_export(tmp_path, [_library()], [LIB_SCRIPT, CALLER]))
        assert xref.library_stages('LIB') == (5, 10)
        assert xref.library_stages('"lib"') == (5, 10)
        assert xref.library_stages('Other') == ()
        assert xref.library_stages('') == ()

    def test_a_quest_that_may_not_repeat_a_stage_is_not(self, tmp_path):
        """Without the repeat flag a second call would do nothing."""
        assert _stages(tmp_path, [_library(flags=1)]) == ()

    def test_a_quest_without_a_script_is_not(self, tmp_path):
        """No script, or a null one, means no variables to pass values in."""
        assert _stages(tmp_path, [_library(scri='')]) == ()
        assert _stages(tmp_path / 'zero', [_library(scri='00000000')]) == ()

    def test_a_quest_with_no_stage_is_not(self, tmp_path):
        """A quest with no stage has nothing to call."""
        assert _stages(tmp_path, [_library(stages=[])]) == ()

    def test_journal_text_on_a_stage_disqualifies(self, tmp_path):
        """A stage that writes the journal is a real quest stage."""
        stages = [_stage(0, 5, 'set Lib.out to 1'),
                  _stage(1, 10, _src('setstage Lib 5'), Text='Told you so.')]
        assert _stages(tmp_path, [_library(stages=stages)]) == ()

    def test_a_condition_on_a_stage_disqualifies(self, tmp_path):
        """A stage entry with a condition is chosen by the engine."""
        stages = [_stage(0, 5, 'set Lib.out to 1'),
                  _stage(1, 10, _src('setstage Lib 5'), ConditionCount=1)]
        assert _stages(tmp_path, [_library(stages=stages)]) == ()

    def test_a_stage_that_completes_the_quest_disqualifies(self, tmp_path):
        """A stage that completes the quest is a real quest stage."""
        stages = [_stage(0, 5, 'set Lib.out to 1'),
                  _stage(1, 10, _src('setstage Lib 5'), Flags=1)]
        assert _stages(tmp_path, [_library(stages=stages)]) == ()

    def test_a_stage_with_two_entries_disqualifies(self, tmp_path):
        """Two log entries on one stage are not one function."""
        stages = [_stage(0, 5, 'set Lib.out to 1'),
                  dict(_stage(1, 10, _src('setstage Lib 5')),
                       **{'Stage[1].LogCount': 2})]
        assert _stages(tmp_path, [_library(stages=stages)]) == ()

    def test_a_stage_with_no_result_script_disqualifies(self, tmp_path):
        """A stage with only blanks as its script disqualifies the quest."""
        stages = [_stage(0, 5, '  '), _stage(1, 10, _src('setstage Lib 5'))]
        assert _stages(tmp_path, [_library(stages=stages)]) == ()

    def test_a_caller_that_reads_a_result_back_marks_a_flat_library(self, tmp_path):
        """No stage calls another, but a caller reads `Lib.out` after the call."""
        flat = [_stage(0, 5, 'set Lib.out to Lib.in1 * 2')]
        reader = _script('00003000', 'CallerScript', 0, 'float x', 'begin GameMode',
                         '  set Lib.in1 to x', '  setstage Lib 5',
                         '  set x to Lib.out', 'end')
        assert _stages(tmp_path, [_library(stages=flat)],
                       [LIB_SCRIPT, reader]) == (5,)

    def test_a_quest_whose_stages_only_do_things_is_not(self, tmp_path):
        """Writes after the call, and a read in another block, are no read-back."""
        flat = [_stage(0, 5, 'set Lib.out to 0')]
        writer = _script('00003000', 'CallerScript', 0, 'begin OnActivate',
                         '  set Lib.in1 to 3', '  setstage Lib 5',
                         '  set Lib.in1 to 0', 'end',
                         'begin GameMode', '  if Lib.out > 1', '  endif', 'end')
        assert _stages(tmp_path, [_library(stages=flat)],
                       [LIB_SCRIPT, writer]) == ()

    def test_a_commented_out_call_is_not_a_call(self, tmp_path):
        """A read-back inside a comment after the call does not count."""
        flat = [_stage(0, 5, _src('; setstage Lib 5', 'set Lib.out to 0'))]
        quiet = _script('00003000', 'CallerScript', 0, 'begin OnActivate',
                        '  setstage Lib 5 ; set x to Lib.out', 'end')
        assert _stages(tmp_path, [_library(stages=flat)],
                       [LIB_SCRIPT, quiet]) == ()

    def test_a_script_that_reads_the_stage_disqualifies(self, tmp_path):
        """GetStage / GetStageDone on the quest, in three spellings."""
        for n, line in enumerate(('if GetStage Lib == 10', 'if getstagedone "Lib" 5',
                                  'if ( GetStage(Lib) >= 5 )')):
            reader = _script('00004000', 'ReaderScript', 0, 'begin GameMode',
                             f'  {line}', '  endif', 'end')
            assert _stages(tmp_path / str(n), [_library()],
                           [LIB_SCRIPT, CALLER, reader]) == (), line

    def test_a_commented_out_stage_read_does_not_disqualify(self, tmp_path):
        """A stage read behind a `;` is no read."""
        note = _script('00004000', 'ReaderScript', 0, 'begin GameMode',
                       '  ; if GetStage Lib == 10', 'end')
        assert _stages(tmp_path, [_library()],
                       [LIB_SCRIPT, CALLER, note]) == (5, 10)

    def test_a_read_of_another_quests_stage_does_not_disqualify(self, tmp_path):
        """A quest whose name only starts the same is another quest."""
        other = _script('00004000', 'ReaderScript', 0, 'begin GameMode',
                        '  if GetStage LibTwo == 10', '  endif', 'end')
        assert _stages(tmp_path, [_library()],
                       [LIB_SCRIPT, CALLER, other]) == (5, 10)

    def test_a_condition_that_reads_the_stage_disqualifies(self, tmp_path):
        """A record condition with GetStage or GetStageDone on the quest."""
        for function in (58, 59):
            got = _stages(tmp_path / str(function), [_library()],
                          other={'INFO.txt': _condition(function, LIB_FID)})
            assert got == (), function

    def test_other_conditions_do_not_disqualify(self, tmp_path):
        """A stage read of another quest, or another function on this one."""
        got = _stages(tmp_path, [_library()],
                      other={'INFO.txt': _condition(58, '00001001')
                             + _condition(72, LIB_FID)})
        assert got == (5, 10)


    def test_a_record_without_an_id_is_left_out(self, tmp_path):
        """With no FormID the condition check cannot be made, so: not a library."""
        text = _library().replace('FormID=%s\n' % LIB_FID, '')
        assert 'FormID' not in text
        assert _stages(tmp_path, [text]) == ()

    def test_a_name_that_only_starts_the_same_is_another_quest(self, tmp_path):
        """`setstage LibTwo 5` is no call of `Lib`, and `MyLib.out` no read of it."""
        flat = [_stage(0, 5, 'set Lib.out to Lib.in1 * 2')]
        longer = _script('00003000', 'CallerScript', 0, 'float x', 'begin OnActivate',
                         '  setstage LibTwo 5', '  set x to Lib.out', 'end')
        prefixed = _script('00003000', 'CallerScript', 0, 'float x', 'begin OnActivate',
                           '  setstage Lib 5', '  set x to MyLib.out', 'end')
        assert _stages(tmp_path / 'a', [_library(stages=flat)],
                       [LIB_SCRIPT, longer]) == ()
        assert _stages(tmp_path / 'b', [_library(stages=flat)],
                       [LIB_SCRIPT, prefixed]) == ()

    def test_a_condition_on_any_record_type_counts(self, tmp_path):
        """IDLE, PACK and QUST conditions, and a quest's stage and target conditions."""
        raw = _condition(58, LIB_FID).split('Condition[0].Raw=')[1].split('\n')[0]
        other = {'IDLE.txt': _rec('IDLE', '00009001', 'AnIdle', **{'Condition[0].Raw': raw}),
                 'PACK.txt': _rec('PACK', '00009002', 'APack', **{'Condition[3].Raw': raw})}
        for n, (name, text) in enumerate(other.items()):
            assert _stages(tmp_path / str(n), [_library()], other={name: text}) == (), name
        for n, key in enumerate(('Condition[0].Raw', 'Stage[2].Log[0].Condition[1].Raw',
                                 'Target[0].Condition[0].Raw')):
            quest = _rec('QUST', '00009003', 'AnotherQuest', **{key: raw})
            assert _stages(tmp_path / ('q%d' % n), [_library(), quest]) == (), key

    def test_an_unreadable_condition_file_means_no_library(self, tmp_path, monkeypatch):
        """What a file that cannot be read holds is unknown; the build names no library."""
        from script_convert import cross_ref
        real = cross_ref._condition_lines
        monkeypatch.setattr(cross_ref, '_condition_lines',
                            lambda path: None if path.endswith('PACK.txt') else real(path))
        assert _stages(tmp_path, [_library()],
                       other={'PACK.txt': _rec('PACK', '00009002', 'APack')}) == ()


class TestMasters:
    def _master(self, tmp_path, **kw):
        """Write `Master.esm` holding the library and its script."""
        return _export(tmp_path, [_library(**kw)], [LIB_SCRIPT], name='Master.esm')

    def test_a_masters_library_is_one_for_the_plugin(self, tmp_path):
        """A plugin's caller sees its master's library."""
        self._master(tmp_path)
        plugin = _export(tmp_path, [], [CALLER], masters=('Master.esm',))
        assert _graph(plugin).library_stages('Lib') == (5, 10)

    def test_the_plugins_own_record_replaces_the_masters(self, tmp_path):
        """An override that adds journal text takes the quest off the list."""
        self._master(tmp_path)
        stages = [_stage(0, 5, 'set Lib.out to 1', Text='Now it has a journal.')]
        plugin = _export(tmp_path, [_library(stages=stages)], [CALLER],
                         masters=('Master.esm',))
        assert _graph(plugin).library_stages('Lib') == ()

    def test_the_owners_build_decides_for_every_plugin_built_later(self, tmp_path):
        """A flat library nobody reads back in its own plugin is none for a dependent either.

        The master's build wrote no turn functions; a dependent that reads a
        result back must still emit the engine call, or it would not compile.
        """
        flat = [_stage(0, 5, 'set Lib.out to Lib.in1 * 2')]
        _export(tmp_path, [_library(stages=flat)], [LIB_SCRIPT], name='Master.esm')
        reader = _script('01003000', 'CallerScript', 0, 'float x', 'begin GameMode',
                         '  set Lib.in1 to x', '  setstage Lib 5',
                         '  set x to Lib.out', 'end')
        plugin = _export(tmp_path, [], [reader], masters=('Master.esm',))
        assert _graph(plugin).library_stages('Lib') == ()
        out = tmp_path / 'out'
        convert_all_scripts(str(plugin), str(out))
        body = (out / 'TES4_CallerScript.psc').read_text(encoding='utf-8')
        assert 'TES4_LibScript.TES4SetStage(Lib as TES4_LibScript, 5)' in body
        assert 'TES4_QF_Lib.' not in body

    def test_a_dependents_stage_read_does_not_change_the_owners_answer(self, tmp_path):
        """The master's scripts already call directly; the dependent calls the same way."""
        self._master(tmp_path)
        reader = _script('01004000', 'ReaderScript', 0, 'begin GameMode',
                         '  if GetStage Lib == 10', '  endif', 'end')
        plugin = _export(tmp_path, [], [CALLER, reader], masters=('Master.esm',))
        assert _graph(plugin).library_stages('Lib') == (5, 10)

    def test_a_dependents_condition_does_not_change_the_owners_answer(self, tmp_path):
        """A condition in the plugin that reads the master's library stage: same limit."""
        self._master(tmp_path)
        plugin = _export(tmp_path, [], [CALLER], masters=('Master.esm',),
                         other={'INFO.txt': _condition(58, LIB_FID)})
        assert _graph(plugin).library_stages('Lib') == (5, 10)

    def test_a_condition_in_the_owners_master_is_read_in_the_owners_index_space(
            self, tmp_path):
        """The owner overrides a master's quest; the master's condition names it as 00..."""
        _export(tmp_path, [], [], name='Filler.esm')
        _export(tmp_path, [_library(flags=1)], [LIB_SCRIPT], name='Base.esm',
                other={'INFO.txt': _condition(58, LIB_FID)})
        owner = _export(tmp_path, [_library(fid='01001000')], [CALLER],
                        masters=('Filler.esm', 'Base.esm'))
        assert _graph(owner).library_stages('Lib') == ()
        (tmp_path / 'Base.esm' / 'INFO.txt').write_text('', encoding='utf-8')
        assert _graph(owner).library_stages('Lib') == (5, 10)


def _convert(tmp_path, quests, scripts):
    """Convert a small export; {file name: lines} of the scripts written."""
    export_dir = _export(tmp_path, quests, scripts)
    out = tmp_path / 'out'
    stats = convert_all_scripts(str(export_dir), str(out))
    assert stats['scpt_err'] == 0 and stats['qust_err'] == 0, stats.get('errors')
    return {p.name: p.read_text(encoding='utf-8').split('\n')
            for p in out.glob('*.psc')}


def _index(lines, needle):
    """The index of the one line of `lines` that holds `needle`."""
    hits = [i for i, line in enumerate(lines) if needle in line]
    assert len(hits) == 1, (needle, hits)
    return hits[0]


class TestWhatCallersEmit:
    def test_a_caller_holds_one_turn_around_input_call_and_result(self, tmp_path):
        """Enter, the input write, the call, the read and Leave, adjacent and level."""
        psc = _convert(tmp_path, [_library()], [LIB_SCRIPT, CALLER])
        body = psc['TES4_CallerScript.psc']
        enter = _index(body, 'Int TES4_Turn1 = TES4_QF_Lib.TES4Enter(Lib)')
        write = _index(body, 'Lib.in1 = x')
        call = _index(body, 'TES4_QF_Lib.TES4CallStage(Lib, 10)')
        read = _index(body, 'x = Lib.out')
        leave = _index(body, 'TES4_QF_Lib.TES4Leave(Lib, TES4_Turn1)')
        assert [enter, write, call, read, leave] == list(range(enter, enter + 5))
        assert len({len(body[i]) - len(body[i].lstrip())
                    for i in (enter, write, call, read, leave)}) == 1
        assert not any('TES4SetStage' in line for line in body)

    def test_a_caller_that_touches_no_variable_takes_its_turn_inside_the_call(
            self, tmp_path):
        """A lone call becomes the locked call, with no Enter or Leave."""
        bare = _script('00004000', 'BareScript', 0, 'begin OnActivate',
                       '  setstage Lib 10', 'end')
        body = _convert(tmp_path, [_library()],
                        [LIB_SCRIPT, CALLER, bare])['TES4_BareScript.psc']
        _index(body, 'TES4_QF_Lib.TES4LockedStage(Lib, 10)')
        assert not any('TES4Enter' in line or 'TES4Leave' in line for line in body)

    def test_a_statement_that_does_not_touch_the_library_ends_the_run(self, tmp_path):
        """The run is only the call and its touching neighbours."""
        split = _script('00004000', 'SplitScript', 0, 'float x', 'float y',
                        'begin OnActivate', '  set Lib.in1 to x', '  set y to 2',
                        '  setstage Lib 10', '  set x to Lib.out', '  set y to 3',
                        '  set y to Lib.out', 'end')
        body = _convert(tmp_path, [_library()],
                        [LIB_SCRIPT, CALLER, split])['TES4_SplitScript.psc']
        enter = _index(body, 'TES4Enter(Lib)')
        leave = _index(body, 'TES4Leave(Lib, ')
        assert enter == _index(body, 'TES4CallStage(Lib, 10)') - 1
        assert leave == _index(body, 'x = Lib.out') + 1
        assert _index(body, 'Lib.in1 = x') < enter
        assert _index(body, 'y = Lib.out') > leave

    def test_another_quests_variables_do_not_join_a_run(self, tmp_path):
        """Statements on another quest's variables leave the call alone."""
        other = _rec('QUST', '00005000', 'Other', SCRI='00005001',
                     **{'DATA.Flags': 1})
        other_script = _script('00005001', 'OtherScript', 1, 'float v',
                               'begin GameMode', 'end')
        mixed = _script('00004000', 'MixedScript', 0, 'float x',
                        'begin OnActivate', '  set Other.v to x',
                        '  setstage Lib 10', '  set x to Other.v', 'end')
        body = _convert(tmp_path, [_library(), other],
                        [LIB_SCRIPT, CALLER, other_script,
                         mixed])['TES4_MixedScript.psc']
        _index(body, 'TES4_QF_Lib.TES4LockedStage(Lib, 10)')
        assert not any('TES4Enter' in line or 'TES4Leave' in line for line in body)

    def test_a_branch_that_tests_the_result_joins_the_run(self, tmp_path):
        """`if Lib.out > 1` right after the call is still inside the turn."""
        tested = _script('00004000', 'TestedScript', 0, 'float x',
                         'begin OnActivate', '  set Lib.in1 to x',
                         '  setstage Lib 10', '  if Lib.out > 1', '    set x to 2',
                         '  else', '    set Lib.in1 to 2', '    setstage Lib 5',
                         '  endif', '  set x to 3', 'end')
        body = _convert(tmp_path, [_library()],
                        [LIB_SCRIPT, CALLER, tested])['TES4_TestedScript.psc']
        leave = _index(body, 'TES4Leave(Lib, ')
        closes = [i for i, line in enumerate(body) if line.strip() == 'EndIf'
                  and i > _index(body, 'TES4CallStage(Lib, 10)')]
        assert leave == closes[0] + 1 and leave < _index(body, 'x = 3')
        _index(body, 'TES4_QF_Lib.TES4CallStage(Lib, 5)')
        assert sum('TES4Enter(Lib)' in line for line in body) == 1
        assert not any('TES4LockedStage' in line for line in body)

    def test_a_loop_never_joins_a_run(self, tmp_path):
        """A `while` on the library's result could hold the turn for as long as it spins."""
        looped = _script('00004000', 'LoopScript', 0, 'float x',
                         'begin OnActivate', '  set Lib.in1 to x',
                         '  setstage Lib 10', '  while Lib.out > x',
                         '    set x to x + 1', '  loop', 'end')
        body = _convert(tmp_path, [_library()],
                        [LIB_SCRIPT, CALLER, looped])['TES4_LoopScript.psc']
        leave = _index(body, 'TES4Leave(Lib, ')
        assert leave == _index(body, 'TES4CallStage(Lib, 10)') + 1
        assert leave < next(i for i, line in enumerate(body)
                            if line.strip().startswith('While '))

    def test_a_comment_inside_a_run_does_not_split_it(self, tmp_path):
        """A comment line between the input and the call leaves one run."""
        noted = _script('00004000', 'NotedScript', 0, 'float x',
                        'begin OnActivate', '  set Lib.in1 to x', '  ; the call',
                        '', '  setstage Lib 10', '  set x to Lib.out', 'end')
        body = _convert(tmp_path, [_library()],
                        [LIB_SCRIPT, CALLER, noted])['TES4_NotedScript.psc']
        assert _index(body, 'TES4Enter(Lib)') == _index(body, 'Lib.in1 = x') - 1
        assert sum('TES4Enter(Lib)' in line for line in body) == 1

    def test_two_libraries_in_one_body_each_hold_their_own_turn(self, tmp_path):
        """A statement that touches both sits inside both turns."""
        second = _library(edid='Lob', fid='00006000', scri='00006001', stages=[
            _stage(0, 5, 'set Lob.out to 1'),
            _stage(1, 10, _src('setstage Lob 5', 'set Lob.out to Lob.out + 1'))])
        lob_script = _script('00006001', 'LobScript', 1, 'float in1', 'float out',
                             'begin GameMode', 'end')
        both = _script('00004000', 'BothScript', 0, 'float x', 'begin OnActivate',
                       '  setstage Lib 10', '  set Lob.in1 to Lib.out',
                       '  setstage Lob 10', '  set x to Lob.out', 'end')
        body = _convert(tmp_path, [_library(), second],
                        [LIB_SCRIPT, CALLER, lob_script, both])['TES4_BothScript.psc']
        order = [_index(body, needle) for needle in (
            'TES4_QF_Lib.TES4Enter(Lib)', 'TES4_QF_Lib.TES4CallStage(Lib, 10)',
            'TES4_QF_Lob.TES4Enter(Lob)', 'Lob.in1 = Lib.out',
            'TES4_QF_Lib.TES4Leave(Lib, ', 'TES4_QF_Lob.TES4CallStage(Lob, 10)',
            'x = Lob.out', 'TES4_QF_Lob.TES4Leave(Lob, ')]
        assert order == list(range(order[0], order[0] + 8))

    def test_a_branch_with_an_early_exit_ends_the_run(self, tmp_path):
        """An `if` that can return is never inside a turn."""
        branch = _script('00004000', 'BranchScript', 0, 'float x',
                         'begin OnActivate', '  set Lib.in1 to x',
                         '  setstage Lib 10', '  if Lib.out > 1', '    return',
                         '  endif', '  set x to Lib.out', 'end')
        body = _convert(tmp_path, [_library()],
                        [LIB_SCRIPT, CALLER, branch])['TES4_BranchScript.psc']
        leave = _index(body, 'TES4Leave(Lib, ')
        assert leave == _index(body, 'TES4CallStage(Lib, 10)') + 1
        assert leave < next(i for i, line in enumerate(body) if 'If ' in line
                            and 'Lib.out' in line)

    def test_a_run_inside_a_branch_sits_at_the_branchs_depth(self, tmp_path):
        """The turn is taken and given back at the run's own indentation."""
        nested = _script('00004000', 'NestedScript', 0, 'float x',
                         'begin OnActivate', '  if x > 1', '    set Lib.in1 to x',
                         '    setstage Lib 10', '    set x to Lib.out', '  endif',
                         'end')
        body = _convert(tmp_path, [_library()],
                        [LIB_SCRIPT, CALLER, nested])['TES4_NestedScript.psc']
        enter, leave = _index(body, 'TES4Enter(Lib)'), _index(body, 'TES4Leave(Lib, ')
        write = _index(body, 'Lib.in1 = x')
        indent = len(body[write]) - len(body[write].lstrip())
        assert enter == write - 1 and leave == _index(body, 'x = Lib.out') + 1
        assert body[enter].startswith(' ' * indent + 'Int TES4_Turn')
        assert body[leave].startswith(' ' * indent + 'TES4_QF_Lib.TES4Leave')

    def test_two_calls_in_one_run_share_a_turn_and_two_runs_get_two(self, tmp_path):
        """One turn per run, numbered, and every call inside one is direct."""
        twice = _script('00004000', 'TwiceScript', 0, 'float x', 'float y',
                        'begin OnActivate', '  set Lib.in1 to x', '  setstage Lib 5',
                        '  set Lib.in1 to Lib.out', '  setstage Lib 10',
                        '  set x to Lib.out', '  set y to 1', '  set Lib.in1 to y',
                        '  setstage Lib 5', '  set y to Lib.out', 'end')
        body = _convert(tmp_path, [_library()],
                        [LIB_SCRIPT, CALLER, twice])['TES4_TwiceScript.psc']
        enters = [i for i, line in enumerate(body) if 'TES4Enter(Lib)' in line]
        leaves = [i for i, line in enumerate(body) if 'TES4Leave(Lib, ' in line]
        assert len(enters) == 2 and len(leaves) == 2
        assert 'TES4_Turn1' in body[enters[0]] and 'TES4_Turn1' in body[leaves[0]]
        assert 'TES4_Turn2' in body[enters[1]] and 'TES4_Turn2' in body[leaves[1]]
        assert enters[0] < leaves[0] < enters[1] < leaves[1]
        assert sum('TES4CallStage(Lib, ' in line for line in body) == 3
        assert not any('TES4LockedStage' in line for line in body)

    def test_a_lone_call_after_a_run_takes_its_own_turn(self, tmp_path):
        """After Leave a further call is a locked call again."""
        after = _script('00004000', 'AfterScript', 0, 'float x', 'float y',
                        'begin OnActivate', '  set Lib.in1 to x', '  setstage Lib 5',
                        '  set x to Lib.out', '  set y to 1', '  setstage Lib 10',
                        'end')
        body = _convert(tmp_path, [_library()],
                        [LIB_SCRIPT, CALLER, after])['TES4_AfterScript.psc']
        assert _index(body, 'TES4Leave(Lib, ') < _index(
            body, 'TES4_QF_Lib.TES4LockedStage(Lib, 10)')
        _index(body, 'TES4_QF_Lib.TES4CallStage(Lib, 5)')

    def test_a_quoted_quest_name_is_the_same_quest(self, tmp_path):
        """`setstage "Lib" 10` joins the run on `Lib`."""
        quoted = _script('00004000', 'QuotedScript', 0, 'float x',
                         'begin OnActivate', '  set Lib.in1 to x',
                         '  setstage "Lib" 10', '  set x to Lib.out', 'end')
        body = _convert(tmp_path, [_library()],
                        [LIB_SCRIPT, CALLER, quoted])['TES4_QuotedScript.psc']
        enter = _index(body, 'TES4Enter(Lib)')
        assert _index(body, 'TES4_QF_Lib.TES4CallStage(Lib, 10)') == enter + 2
        assert _index(body, 'TES4Leave(Lib, ') == enter + 4

    def test_a_dialogue_result_keeps_its_statements_in_order(self, tmp_path):
        """The pass that moves writes ahead of a stage call leaves a library call alone."""
        info = _rec('INFO', '00009000', '', ResultScript=_src(
            'set Lib.in1 to 3', 'setstage Lib 10', 'set Lib.in1 to 0'))
        export_dir = _export(tmp_path, [_library()], [LIB_SCRIPT, CALLER],
                             other={'INFO.txt': info})
        out = tmp_path / 'out'
        convert_all_scripts(str(export_dir), str(out))
        body = (out / 'TES4_TIF__00009000.psc').read_text(encoding='utf-8').split('\n')
        order = [_index(body, needle) for needle in (
            'TES4Enter(Lib)', 'Lib.in1 = 3', 'TES4_QF_Lib.TES4CallStage(Lib, 10)',
            'Lib.in1 = 0', 'TES4Leave(Lib, ')]
        assert order == sorted(order)

    def test_no_write_is_moved_across_a_library_call(self, tmp_path):
        """An ordinary stage call first: the writes around the library call stay put."""
        other = _rec('QUST', '00005000', 'Other', SCRI='00005001',
                     **{'DATA.Flags': 1})
        other_script = _script('00005001', 'OtherScript', 1, 'begin GameMode', 'end')
        info = _rec('INFO', '00009000', '', ResultScript=_src(
            'setstage Other 20', 'set Lib.in1 to 3', 'setstage Lib 10',
            'set Lib.in1 to 0'))
        export_dir = _export(tmp_path, [_library(), other],
                             [LIB_SCRIPT, CALLER, other_script],
                             other={'INFO.txt': info})
        out = tmp_path / 'out'
        convert_all_scripts(str(export_dir), str(out))
        body = (out / 'TES4_TIF__00009000.psc').read_text(encoding='utf-8').split('\n')
        order = [_index(body, needle) for needle in (
            'TES4SetStage(Other as TES4_OtherScript, 20)', 'Lib.in1 = 3',
            'TES4_QF_Lib.TES4CallStage(Lib, 10)', 'Lib.in1 = 0')]
        assert order == sorted(order)

    def test_a_quest_that_is_no_library_keeps_the_engine_call(self, tmp_path):
        """Without the repeat flag nothing changes for caller or fragment script."""
        psc = _convert(tmp_path, [_library(flags=1)], [LIB_SCRIPT, CALLER])
        body = psc['TES4_CallerScript.psc']
        _index(body, 'TES4_LibScript.TES4SetStage(Lib as TES4_LibScript, 10)')
        assert not any('TES4_Turn' in line or 'TES4_QF_Lib.' in line for line in body)
        assert not any('TES4TakeTurn' in line for line in psc['TES4_QF_Lib.psc'])


class TestTheLibrarysOwnScript:
    def test_its_stage_code_calls_a_sibling_stage_directly(self, tmp_path):
        """Stage 10 calls the function of stage 5 and takes no turn."""
        frag = _convert(tmp_path, [_library()], [LIB_SCRIPT, CALLER])['TES4_QF_Lib.psc']
        start = _index(frag, 'Function Fragment_Stage_0010_Item_0()')
        assert frag[start + 1].strip() == 'Fragment_Stage_0005_Item_0()'
        stage_code = frag[:_index(frag, 'Int TES4_TurnOwner = 0')]
        assert not any(word in line for line in stage_code
                       for word in ('TES4SetStage', 'TES4Enter', 'TES4CallStage',
                                    'TES4LockedStage'))

    def test_a_stage_number_held_in_a_variable_goes_through_the_chain(self, tmp_path):
        """A stage number that is not a literal is dispatched at run time."""
        stages = [_stage(0, 5, 'set Lib.out to Lib.in1 * 2'),
                  _stage(1, 10, _src('setstage Lib Lib.which', 'set Lib.out to Lib.out + 1'))]
        frag = _convert(tmp_path, [_library(stages=stages)],
                        [LIB_SCRIPT, CALLER])['TES4_QF_Lib.psc']
        start = _index(frag, 'Function Fragment_Stage_0010_Item_0()')
        assert frag[start + 1].strip() == 'TES4RunStage(Lib.which)'

    def test_it_carries_the_turn_and_the_direct_call(self, tmp_path):
        """The fragment script declares every function its callers use."""
        frag = _convert(tmp_path, [_library()], [LIB_SCRIPT, CALLER])['TES4_QF_Lib.psc']
        for needle in ('Int Function TES4TakeTurn()',
                       'Function TES4EndTurn(Int aiTurn)',
                       'Bool Function TES4RunStage(Int aiStage)',
                       'Int Function TES4Enter(Quest akQuest) Global',
                       'Function TES4Leave(Quest akQuest, Int aiTurn) Global',
                       'Bool Function TES4CallStage(Quest akQuest, Int aiStage) Global',
                       'Bool Function TES4LockedStage(Quest akQuest, Int aiStage) Global'):
            _index(frag, needle)


class TestLibraryFunctions:
    LINES = library_functions('TES4_QF_Lib', 'TES4_LibScript', (5, 10))

    def _body(self, header):
        """The lines between `header` and its EndFunction."""
        start = self.LINES.index(header)
        return self.LINES[start + 1:self.LINES.index('EndFunction', start)]

    def test_every_stage_is_one_arm_and_an_unknown_one_goes_to_the_engine(self):
        """The dispatch chain, line for line."""
        assert self._body('Bool Function TES4RunStage(Int aiStage)') == [
            '  If aiStage == 5', '    Fragment_Stage_0005_Item_0()',
            '  ElseIf aiStage == 10', '    Fragment_Stage_0010_Item_0()',
            '  Else', '    Return SetStage(aiStage)', '  EndIf', '  Return True']

    def test_a_turn_is_taken_only_after_the_wait_and_never_shared(self):
        """The wait, then a fresh number, with no call in between."""
        body = self._body('Int Function TES4TakeTurn()')
        assert body == ['  Int tries = 0',
                        f'  While TES4_TurnOwner != 0 && tries < {TURN_TRIES}',
                        '    Utility.Wait(0.01)', '    tries += 1', '  EndWhile',
                        '  If TES4_TurnOwner != 0',
                        '    Debug.Trace("TES4_QF_Lib: turn " + TES4_TurnOwner'
                        ' + " was not given back; taken over")',
                        '  EndIf',
                        '  TES4_TurnNext += 1', '  TES4_TurnOwner = TES4_TurnNext',
                        '  Return TES4_TurnOwner']

    def test_only_the_holder_gives_a_turn_back(self):
        """Giving back a turn that was taken over changes nothing."""
        assert self._body('Function TES4EndTurn(Int aiTurn)') == [
            '  If TES4_TurnOwner == aiTurn', '    TES4_TurnOwner = 0', '  EndIf']

    def test_a_stopped_quest_is_started_with_its_variables_kept(self):
        """The start goes through the quest script's own TES4Start."""
        body = self._body('Bool Function TES4CallStage(Quest akQuest, Int aiStage) Global')
        assert body[:4] == ['  If !akQuest.IsRunning()',
                            '    Debug.Trace("TES4_QF_Lib: the quest was not running; starting it")',
                            '    TES4_LibScript.TES4Start(akQuest as TES4_LibScript)',
                            '  EndIf']
        assert '    Return akQuest.SetStage(aiStage)' in body
        assert body[-1] == '  Return library.TES4RunStage(aiStage)'

    def test_a_quest_script_the_graph_cannot_name_starts_plainly(self):
        """With no script type to name, the quest is simply started."""
        lines = library_functions('TES4_QF_Lib', 'Quest', (5,))
        assert '    akQuest.Start()' in lines
        assert not any('TES4Start' in line for line in lines)

    def test_the_lone_call_gives_its_turn_back(self):
        """Enter, the direct call, Leave, in that order."""
        assert self._body('Bool Function TES4LockedStage(Quest akQuest, Int aiStage) Global') == [
            '  Int turn = TES4Enter(akQuest)',
            '  Bool done = TES4CallStage(akQuest, aiStage)',
            '  TES4Leave(akQuest, turn)', '  Return done']

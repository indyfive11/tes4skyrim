"""A plugin adopts support records from masters at any depth of its chain.

See: docs/commentary/tes5_import_pipeline.md#phase-0-dependent-skips-support-records
"""

import os

from tes5_import.base.text_reader import set_formid_index_offset
from tes5_import.overrides.nested import inherited_masters, reconcile_masters
from tes5_import.record_types.actor_common import (origin_memberships,
                                                   reset_origin_faction)


class _OriginIndex:
    """A master index holding an origin FACT in slots 1 (inherited), 2 and 3."""

    def find_all_by_edid(self, _sig, _edid):
        """One origin faction per master slot."""
        return [0x01687124, 0x029A7124, 0x03BB7124]


def test_inherited_master_origin_is_never_joined():
    """Skyrim.esm, Oblivion.esm (inherited), then the header's two masters."""
    set_formid_index_offset(2)
    try:
        reset_origin_faction(_OriginIndex())
        assert origin_memberships() == [0x029A7124, 0x03BB7124]
    finally:
        set_formid_index_offset(0)
        reset_origin_faction()


def _export(root, name: str, masters: list) -> str:
    """An export record folder for `name` whose header lists `masters`."""
    folder = os.path.join(root, name)
    os.makedirs(folder)
    with open(os.path.join(folder, '_HEADER.txt'), 'w', encoding='utf-8') as fh:
        fh.writelines(f'Master[{i}]={m}\n' for i, m in enumerate(masters))
    return folder


def _converted(out_root, name: str) -> None:
    """A converted plugin at `out_root/<name>/<name>`."""
    os.makedirs(os.path.join(out_root, name))
    open(os.path.join(out_root, name, name), 'wb').close()


def test_deeper_masters_are_inherited_deepest_first(tmp_path):
    """Base <- Mid <- Top <- Child: Child's header names only Mid and Top."""
    root, out = str(tmp_path / 'export'), str(tmp_path / 'output')
    _export(root, 'Base.esm', [])
    _export(root, 'Mid.esm', ['Base.esm'])
    _export(root, 'Top.esm', ['Mid.esm'])
    child = _export(root, 'Child.esp', ['Top.esm'])
    for name in ('Base.esm', 'Mid.esm', 'Top.esm'):
        _converted(out, name)
    assert inherited_masters(child, out) == ['Base.esm', 'Mid.esm']


def test_unconverted_deeper_master_is_not_inherited(tmp_path):
    """A deeper master with no converted plugin cannot supply a record."""
    root, out = str(tmp_path / 'export'), str(tmp_path / 'output')
    _export(root, 'Base.esm', [])
    child = _export(root, 'Child.esp', ['Mid.esm'])
    _export(root, 'Mid.esm', ['Base.esm'])
    _converted(out, 'Mid.esm')
    assert inherited_masters(child, out) == []


def test_inherited_masters_sit_between_skyrim_and_the_header():
    """Every export index then shifts by the same amount."""
    got = reconcile_masters(['Skyrim.esm', 'Morrowind.esm'],
                            ['Mid.esm', 'Top.esm'], ['Base.esm'])
    assert got == ['Skyrim.esm', 'Base.esm', 'Mid.esm', 'Top.esm']

"""
The masters' export records, indexed by signature ONCE per run.

`load_master_export` returns a `MasterExport`; every phase that wants the
masters' records of some types asks `records_of` instead of walking the whole
export again.
"""

import heapq
from array import array


class MasterExport(dict):
    """`load_master_export`'s records by re-keyed FormID; `of` answers by signature
    from one index of positions and keys (records are looked up, not copied)."""

    def __init__(self, *args):
        """A dict of the records; the signature index is built on first use."""
        super().__init__(*args)
        self._by_sig = None

    def of(self, sigs) -> list:
        """`(key, record)` of every signature in `sigs`, in the export's own order."""
        if self._by_sig is None:
            self._by_sig = {}
            for pos, (key, rec) in enumerate(self.items()):
                positions, keys = self._by_sig.setdefault(rec.get('Signature'),
                                                          (array('I'), []))
                positions.append(pos)
                keys.append(key)
        groups = [self._by_sig[sig] for sig in set(sigs) if sig in self._by_sig]
        if len(groups) == 1:
            return [(key, self[key]) for key in groups[0][1]]
        merged = heapq.merge(*(zip(positions, keys) for positions, keys in groups))
        return [(key, self[key]) for _pos, key in merged]


def records_of(master_export, *sigs) -> list:
    """`(key, record)` of `sigs` in `master_export` (a `MasterExport`, a plain dict, or None)."""
    if not master_export:
        return []
    if not isinstance(master_export, MasterExport):
        master_export = MasterExport(master_export)
    return master_export.of(sigs)


def values_of(master_export, *sigs) -> list:
    """The records of `sigs` in `master_export`, as `records_of` orders them."""
    return [rec for _key, rec in records_of(master_export, *sigs)]

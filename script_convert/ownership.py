"""Which plugin wrote which script in a `scripts/` folder several plugins share.

An imported mod's plugins convert into ONE output folder, so their scripts sit
side by side. Each plugin's run records the names it wrote in
`scripts/<owner>.owned.txt`; a rebuild clears only its own, and the compiler
compiles only its own.

See: docs/commentary/script_convert.md#wipe-output-dir
"""

import os

#: Suffix of the per-plugin list of scripts written into a shared `scripts/` folder.
OWNED_SUFFIX = '.owned.txt'


def owner_key(export_dir) -> str:
    """The name a plugin's ownership list is filed under: its record folder's."""
    return os.path.basename(os.path.normpath(str(export_dir)))


def owned_list_path(source_dir, owner: str) -> str:
    """`scripts/<owner>.owned.txt`, beside the `scripts/source/` tree `source_dir`."""
    return os.path.join(os.path.dirname(str(source_dir)), owner + OWNED_SUFFIX)


def read_owned(source_dir, owner: str) -> set:
    """The script names `owner`'s last run wrote; empty when it kept no list."""
    try:
        with open(owned_list_path(source_dir, owner), encoding='utf-8') as fh:
            return {line.strip() for line in fh if line.strip()}
    except OSError:
        return set()


def write_owned(source_dir, owner: str, names) -> None:
    """Record the script names `owner` wrote this run."""
    with open(owned_list_path(source_dir, owner), 'w', encoding='utf-8') as fh:
        fh.write(''.join(f'{n}\n' for n in sorted(set(names))))


def sibling_owned(source_dir, owner: str) -> set:
    """Every script name another plugin sharing this folder claims."""
    pex_dir = os.path.dirname(str(source_dir))
    names = os.listdir(pex_dir) if os.path.isdir(pex_dir) else []
    others = [n[:-len(OWNED_SUFFIX)] for n in names
              if n.endswith(OWNED_SUFFIX) and n != owner + OWNED_SUFFIX]
    return set().union(*(read_owned(source_dir, o) for o in others))

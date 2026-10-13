"""Add-on: Oblivion's weapon-hit rule on hits a converted actor deals or takes.

See: extensions/oblivion_weapon_rule/README.md
"""

from .rule import actor_subrecords, create_records, reset

__all__ = ['actor_subrecords', 'create_records', 'reset']

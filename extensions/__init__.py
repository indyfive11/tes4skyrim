"""Optional add-ons to the conversion, one package each.

An add-on is loaded only when ``"extensions"`` in conversion_config.json names
it. Its package defines at least one of the hooks ``create_records(writer,
context)``, ``actor_subrecords(kind, rec)`` and ``weapon_keywords(rec,
anim_type)``, may define ``reset()``, and imports nothing of the converter
beyond the surface ``core/extensions.py`` documents. The converter never
imports an add-on by name.

See: extensions/README.md
"""

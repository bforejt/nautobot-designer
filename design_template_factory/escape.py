"""Jinja injection safety for generated designs.

Design Builder renders every design file through Jinja2 before parsing the
YAML (verified; and its issue #258 was exactly this vulnerability class in
job inputs). A generated design embeds literal values captured from a live
site — descriptions, names, custom-field text. Any literal containing Jinja
delimiters would execute with ORM access in the render context unless
neutralized here.

Quoting contract (kept in lockstep with render/design.py's YAML dump):
generated Jinja uses ONLY single quotes, and the renderer forces YAML
double-quoted style for all scalars. YAML double-quoting backslash-escapes
double quotes and non-printables but never single quotes, so the emitted
Jinja survives the dump byte-exact; the reverse combination (double-quoted
Jinja or single-quoted YAML) corrupts one side or the other.

Rule: only Placeholder values (produced by the renderer itself) may carry
live Jinja syntax; every captured literal passes through escape_literal().
"""

from __future__ import annotations

import re

_JINJA_DELIMITERS = re.compile(r"(\{\{|\}\}|\{%|%\}|\{#|#\})")


class Placeholder(str):
    """A renderer-generated Jinja expression, exempt from escaping.

    Must contain no double-quote characters (see module quoting contract).
    """

    __slots__ = ()

    def __new__(cls, value: str):
        if '"' in value:
            raise ValueError(
                f"Placeholder may not contain double quotes (quoting contract): {value!r}"
            )
        return super().__new__(cls, value)


def escape_literal(value: str) -> str:
    """Neutralize Jinja delimiters in a captured literal.

    Each delimiter token is replaced with a single-quoted Jinja string
    expression that renders back to the original characters, so the deployed
    object carries the source site's literal text but nothing executes.
    """
    if isinstance(value, Placeholder):
        return str(value)
    return _JINJA_DELIMITERS.sub(lambda m: "{{ '" + m.group(1) + "' }}", value)


def escape_tree(value):
    """Recursively escape every plain string in a JSON-ish structure."""
    if isinstance(value, Placeholder):
        return str(value)
    if isinstance(value, str):
        return escape_literal(value)
    if isinstance(value, dict):
        return {escape_tree(k): escape_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [escape_tree(item) for item in value]
    return value

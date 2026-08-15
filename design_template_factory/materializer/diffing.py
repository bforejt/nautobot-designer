"""Deep-diff a resolved plan against a captured spec — the acceptance gate.

verify = capture(deployed site) vs resolve(template, seeds). Pure Python.

Normalization rules (documented, deliberate):
- `provisioned_from` is stripped from every custom_fields dict (deploy adds it).
- `vlan_groups` is excluded (the capture walker does not model group
  membership; the per-site group's existence is asserted by the executor).
- None values and empty custom_fields dicts are dropped (capture emits None
  for absent optionals; resolve may omit them).
- `_components.removals` ARE compared: the executor deletes removals, so the
  re-captured device should show the same removals-vs-template delta.
- Cables are orientation-normalized (endpoints sorted within each cable).
- Every family is sorted by a stable identity key before comparison.
- `_meta`/`_references`/lint are out of scope (verification is object-level).
"""

from __future__ import annotations

from typing import Any

from .. import constants

_EXCLUDED_FAMILIES = {"vlan_groups"}

_SORT_KEYS = {
    "locations": lambda o: o.get("path", ""),
    "rack_groups": lambda o: o.get("name", ""),
    "racks": lambda o: o.get("name", ""),
    "power_panels": lambda o: o.get("name", ""),
    "power_feeds": lambda o: (o.get("power_panel", ""), o.get("name", "")),
    "vlans": lambda o: o.get("vid", 0),
    "vrfs": lambda o: o.get("name", ""),
    "prefixes": lambda o: o.get("prefix", ""),
    "devices": lambda o: o.get("name", ""),
    "ip_addresses": lambda o: o.get("address", ""),
    "ip_assignments": lambda o: (o.get("device", ""), o.get("interface", ""), o.get("ip", "")),
    "primary_ips": lambda o: (o.get("device", ""), o.get("family", 4)),
    "cables": lambda o: (str(o.get("a")), str(o.get("b"))),
}


def _clean(node: Any) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if value is None:
                continue
            if key == "custom_fields":
                value = {k: v for k, v in value.items() if k != "provisioned_from" and v is not None}
                if not value:
                    continue
            cleaned = _clean(value)
            if cleaned in ({}, []):
                continue
            out[key] = cleaned
        return out
    if isinstance(node, list):
        return [_clean(item) for item in node]
    return node


def _normalize_components(components: dict) -> dict:
    out = {}
    for family, buckets in sorted((components or {}).items()):
        fam = {}
        for bucket in ("overrides", "additions"):
            entries = sorted(
                (_clean(c) for c in buckets.get(bucket, [])), key=lambda c: c.get("name", "")
            )
            if entries:
                fam[bucket] = entries
        removals = sorted(buckets.get("removals", []))
        if removals:
            fam["removals"] = removals
        if fam:
            out[family] = fam
    return out


def normalize(plan_or_spec: dict) -> dict:
    """Reduce a resolved plan OR a captured spec's object families to
    comparable canonical form."""
    out: dict[str, list] = {}
    for family in constants.CREATION_ORDER:
        if family in _EXCLUDED_FAMILIES:
            continue
        entries = []
        for obj in plan_or_spec.get(family, []):
            obj = dict(obj)
            if family == "devices":
                obj["_components"] = _normalize_components(obj.get("_components") or {})
                if not obj["_components"]:
                    obj.pop("_components")
            if family == "cables":
                a, b = obj.get("a"), obj.get("b")
                if a is not None and b is not None and [str(x) for x in b] < [str(x) for x in a]:
                    obj["a"], obj["b"] = b, a
            entries.append(_clean(obj))
        entries.sort(key=_SORT_KEYS.get(family, lambda o: str(o)))
        if entries:
            out[family] = entries
    return out


def deep_diff(expected: dict, actual: dict, path: str = "") -> list[str]:
    """Human-readable list of differences; empty list == match."""
    diffs: list[str] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            sub = f"{path}.{key}" if path else str(key)
            if key not in expected:
                diffs.append(f"unexpected {sub}: {actual[key]!r}")
            elif key not in actual:
                diffs.append(f"missing {sub}: expected {expected[key]!r}")
            else:
                diffs.extend(deep_diff(expected[key], actual[key], sub))
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            diffs.append(f"{path}: length {len(actual)} != expected {len(expected)}")
        for index, (e, a) in enumerate(zip(expected, actual)):
            diffs.extend(deep_diff(e, a, f"{path}[{index}]"))
    elif expected != actual:
        diffs.append(f"{path}: {actual!r} != expected {expected!r}")
    return diffs


def diff_deployment(resolved_plan: dict, captured_spec_objects: dict) -> list[str]:
    """The verify gate: resolved plan (expected) vs captured objects (actual)."""
    return deep_diff(normalize(resolved_plan), normalize(captured_spec_objects))

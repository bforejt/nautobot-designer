"""Parameter map: model, loading, and the mechanical proposer.

The parameter map is the human "intent" file (plan §3.2): the
renaming/renumbering policy no tool can infer, reduced to a mechanical
proposal plus a short human review. It lives in git next to the spec.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import constants
from .spec import SiteSpec
from . import rewrite


class ParamMapError(ValueError):
    """A parameter map failed validation."""


@dataclass
class ParamMap:
    template_id: str
    source_site_code: str
    supernets: list[dict[str, str]]  # [{seed, source, description}]
    name_patterns: list[dict[str, str]]  # [{pattern, replace}]
    vlan_group_name: str = "{{ site_code }}-vlans"
    drop_custom_fields: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ParamMap":
        schema = raw.get("schema")
        if schema != constants.PARAM_MAP_SCHEMA_VERSION:
            raise ParamMapError(
                f"param map schema {schema!r} unsupported "
                f"(expected {constants.PARAM_MAP_SCHEMA_VERSION!r})"
            )
        template = raw.get("template") or {}
        rules = raw.get("rules") or {}
        pmap = cls(
            template_id=template.get("id") or "site-template",
            source_site_code=template.get("source_site_code") or "",
            supernets=list(raw.get("supernets") or []),
            name_patterns=list(rules.get("name_patterns") or []),
            vlan_group_name=rules.get("vlan_group_name", "{{ site_code }}-vlans"),
            drop_custom_fields=list(rules.get("drop_custom_fields") or []),
            notes=list(raw.get("notes") or []),
        )
        pmap.validate()
        return pmap

    @classmethod
    def load(cls, path: str | Path) -> "ParamMap":
        with open(path, encoding="utf-8") as handle:
            return cls.from_dict(yaml.safe_load(handle))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": constants.PARAM_MAP_SCHEMA_VERSION,
            "template": {
                "id": self.template_id,
                "source_site_code": self.source_site_code,
            },
            "supernets": self.supernets,
            "rules": {
                "name_patterns": self.name_patterns,
                "vlan_group_name": self.vlan_group_name,
                "drop_custom_fields": self.drop_custom_fields,
            },
            "notes": self.notes,
        }

    def save(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(self.to_dict(), handle, sort_keys=False, width=100)

    def validate(self) -> None:
        if not self.source_site_code:
            raise ParamMapError("template.source_site_code is required")
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", self.template_id):
            raise ParamMapError(
                f"template.id {self.template_id!r} must be a lowercase slug "
                "(it becomes a Python module and job class name)"
            )
        seeds = [s.get("seed") for s in self.supernets]
        if len(seeds) != len(set(seeds)):
            raise ParamMapError("duplicate supernet seed names")
        import ipaddress as _ip

        for entry in self.supernets:
            if not entry.get("seed") or not entry.get("source"):
                raise ParamMapError(f"malformed supernet entry: {entry!r}")
            try:
                _ip.ip_network(entry["source"])
            except ValueError as err:
                raise ParamMapError(
                    f"supernet source {entry['source']!r}: {err}"
                ) from err
            if not re.fullmatch(r"[a-z][a-z0-9_]*", entry["seed"]):
                raise ParamMapError(
                    f"seed {entry['seed']!r} must be a lowercase identifier "
                    "(it becomes a job variable name)"
                )
        for rule in self.name_patterns:
            if "pattern" not in rule or "replace" not in rule:
                raise ParamMapError(f"malformed name pattern: {rule!r}")
            re.compile(rule["pattern"])

    # ------------------------------------------------------------ accessors
    @property
    def source_roots(self) -> list[str]:
        return [entry["source"] for entry in self.supernets]

    def seed_for_root(self, root: str) -> str:
        for entry in self.supernets:
            if entry["source"] == root:
                return entry["seed"]
        raise ParamMapError(f"no seed defined for source supernet {root}")


def propose(spec: SiteSpec) -> ParamMap:
    """Mechanically propose a parameter map from a captured spec.

    The output is a *proposal*: a human reviews and commits it. Detection
    failures become notes, never silent choices.
    """
    site_code = spec.source_site_code
    notes: list[str] = []

    # Supernet seeds from the minimal covering set of captured prefixes.
    prefixes = [p["prefix"] for p in spec.entries("prefixes")]
    roots = rewrite.compute_roots(prefixes) if prefixes else []
    supernets = [
        {
            "seed": f"supernet_{index}",
            "source": root,
            "description": f"Replaces source supernet {root}",
        }
        for index, root in enumerate(roots, start=1)
    ]
    # Every captured IP must land inside a root, or re-prefixing can't work.
    for ip in spec.entries("ip_addresses"):
        if rewrite.find_root(ip["address"], roots) is None:
            notes.append(
                f"IP {ip['address']} is outside every detected supernet — "
                "add a covering supernet entry or it cannot be re-prefixed"
            )

    # Name patterns: detect the source site code embedded in names. The
    # emitted pattern is case-insensitive ((?i)) so detection and rewriting
    # agree — a device named 'dal01-sw1' must not silently skip the rewrite.
    patterns: list[dict[str, str]] = []
    if site_code:
        pattern = re.escape(site_code)
        hit = any(
            re.search(pattern, obj.get("name", ""), flags=re.IGNORECASE)
            for family in ("devices", "racks", "rack_groups", "locations")
            for obj in spec.entries(family)
        )
        if hit:
            patterns.append({"pattern": f"(?i){pattern}", "replace": "{{ site_code }}"})
        else:
            notes.append(
                f"source site code {site_code!r} was not found in any object name; "
                "add name_patterns manually — devices/racks without site-coded names "
                "are emitted with plain !create (see identity.py policy)"
            )

    for family in ("devices", "racks", "rack_groups"):
        for obj in spec.entries(family):
            name = obj.get("name", "")
            if site_code and site_code.lower() not in name.lower():
                notes.append(
                    f"{family[:-1]} {name!r} does not embed the site code; it will "
                    "not be renamed by the proposed patterns (review required)"
                )

    return ParamMap(
        template_id=f"{site_code.lower()}-template" if site_code else "site-template",
        source_site_code=site_code,
        supernets=supernets,
        name_patterns=patterns,
        notes=notes,
    )

"""Site-spec model: load, save, validate.

The spec is the system of record (capture-to-design-plan.md §3.1). It is a
plain JSON document shaped as an annotated superset of a Design Builder
design dict, so the renderer is mostly a projection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import constants


class SpecError(ValueError):
    """A spec failed structural validation."""


@dataclass
class SiteSpec:
    meta: dict[str, Any]
    references: dict[str, list[dict[str, Any]]]
    parameters: dict[str, Any] = field(default_factory=dict)
    objects: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    # ------------------------------------------------------------------ I/O
    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SiteSpec":
        meta = raw.get(constants.META_KEY)
        if not isinstance(meta, dict):
            raise SpecError(f"spec is missing the {constants.META_KEY} section")

        schema = meta.get("spec_schema")
        if schema != constants.SPEC_SCHEMA_VERSION:
            raise SpecError(
                f"spec schema {schema!r} is not supported by this tool "
                f"(expected {constants.SPEC_SCHEMA_VERSION!r}); re-capture the site"
            )

        references = raw.get(constants.REFERENCES_KEY, {})
        if not isinstance(references, dict):
            raise SpecError(f"{constants.REFERENCES_KEY} must be a mapping")

        objects: dict[str, list[dict[str, Any]]] = {}
        for key in constants.CREATION_ORDER:
            entries = raw.get(key, [])
            if not isinstance(entries, list):
                raise SpecError(f"spec key {key!r} must be a list")
            objects[key] = entries

        unknown = (
            set(raw)
            - set(constants.CREATION_ORDER)
            - {constants.META_KEY, constants.REFERENCES_KEY, constants.PARAMETERS_KEY}
        )
        if unknown:
            raise SpecError(
                f"spec contains unknown keys {sorted(unknown)!r}; "
                "spec schema and tool version are out of step"
            )

        return cls(
            meta=meta,
            references=references,
            parameters=raw.get(constants.PARAMETERS_KEY, {}) or {},
            objects=objects,
        )

    @classmethod
    def load(cls, path: str | Path) -> "SiteSpec":
        with open(path, encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    def to_dict(self) -> dict[str, Any]:
        raw: dict[str, Any] = {
            constants.META_KEY: self.meta,
            constants.REFERENCES_KEY: self.references,
            constants.PARAMETERS_KEY: self.parameters,
        }
        for key in constants.CREATION_ORDER:
            raw[key] = self.objects.get(key, [])
        return raw

    def save(self, path: str | Path) -> None:
        # sort_keys=False: creation order in the file mirrors execution order,
        # and stable output keeps specs git-diffable.
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, sort_keys=False)
            handle.write("\n")

    # ------------------------------------------------------------ accessors
    @property
    def source_site_code(self) -> str:
        try:
            return self.meta["source"]["site_code"]
        except KeyError as err:
            raise SpecError("spec _meta.source.site_code is missing") from err

    def entries(self, key: str) -> list[dict[str, Any]]:
        return self.objects.get(key, [])

    # ----------------------------------------------------------- validation
    def validate(self) -> list[str]:
        """Structural sanity checks; returns a list of problems (empty = ok)."""
        problems: list[str] = []

        for family in constants.REFERENCE_FAMILIES:
            if family not in self.references:
                problems.append(f"_references.{family} is missing (capture bug?)")

        for device_type in self.references.get("device_types", []):
            if "template_fingerprint" not in device_type:
                problems.append(
                    "device_type reference "
                    f"{device_type.get('manufacturer')}/{device_type.get('model')} "
                    "lacks a template_fingerprint (drift guard)"
                )

        device_names = {d.get("name") for d in self.entries("devices")}
        if len(device_names) != len(self.entries("devices")):
            problems.append("duplicate device names in spec")

        # Locations are keyed by root-relative path (names are only unique
        # per parent); every location reference must resolve to a known path.
        location_paths: set[str] = set()
        for loc in self.entries("locations"):
            path = loc.get("path")
            if not path:
                problems.append(f"location {loc.get('name')!r} has no path")
                continue
            if path in location_paths:
                problems.append(f"duplicate location path {path!r}")
            location_paths.add(path)
            if loc.get("parent") and loc["parent"] not in location_paths:
                problems.append(
                    f"location {path!r} parent {loc['parent']!r} not defined before use"
                )
        for family in ("rack_groups", "racks", "power_panels", "devices"):
            for obj in self.entries(family):
                if obj.get("location") and obj["location"] not in location_paths:
                    problems.append(
                        f"{family[:-1]} {obj.get('name')!r} references unknown "
                        f"location path {obj['location']!r}"
                    )

        panel_names = {p.get("name") for p in self.entries("power_panels")}

        for assignment in self.entries("ip_assignments"):
            if assignment.get("device") not in device_names:
                problems.append(
                    f"ip_assignment references unknown device {assignment.get('device')!r}"
                )

        for primary in self.entries("primary_ips"):
            if primary.get("device") not in device_names:
                problems.append(
                    f"primary_ip references unknown device {primary.get('device')!r}"
                )

        for cable in self.entries("cables"):
            for end in ("a", "b"):
                endpoint = cable.get(end)
                if not (isinstance(endpoint, list) and len(endpoint) == 3):
                    problems.append(f"cable endpoint {end!r} malformed: {endpoint!r}")
                    continue
                owner, family, _name = endpoint
                if family not in constants.CABLE_ENDPOINT_TYPES.values():
                    problems.append(f"cable endpoint family {family!r} unsupported")
                elif family == "power_feeds" and owner not in panel_names:
                    problems.append(
                        f"cable power-feed endpoint panel {owner!r} not in spec"
                    )
                elif family != "power_feeds" and owner not in device_names:
                    problems.append(f"cable endpoint device {owner!r} not in spec")

        return problems

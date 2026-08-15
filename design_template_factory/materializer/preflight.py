"""Pre-flight validation — every failure surfaces before a single write.

Salvaged from the generated-validator logic of the retired render arm, now
plain functions run by the deploy job before the transaction opens. Imports
Nautobot lazily so the pure modules stay importable anywhere.
"""

from __future__ import annotations

import ipaddress

from ..params import ParamMap
from ..spec import SiteSpec
from .resolver import Seeds

PROVISIONED_FROM_FIELD = "provisioned_from"


class PreflightError(ValueError):
    """A pre-flight check failed; nothing was written."""


def _fail(message: str) -> None:
    raise PreflightError(message)


def run_preflight(spec: SiteSpec, pmap: ParamMap, seeds: Seeds, resolved_plan: dict) -> list[str]:
    """Run all checks; returns informational notes. Raises PreflightError."""
    notes: list[str] = []
    _check_references(spec)
    _check_custom_fields(spec)
    _check_parent_location(spec, seeds)
    _check_name_collisions(resolved_plan)
    _check_supernets(pmap, seeds)
    notes.append("preflight passed")
    return notes


def _check_references(spec: SiteSpec) -> None:
    from nautobot.dcim.models import DeviceType, LocationType, Manufacturer, Platform
    from nautobot.extras.models import Role, Status, Tag
    from nautobot.ipam.models import Namespace
    from nautobot.tenancy.models import Tenant

    from ..fingerprint import fingerprint_device_type

    lookups = {
        "statuses": (Status, "name"),
        "roles": (Role, "name"),
        "tags": (Tag, "name"),
        "manufacturers": (Manufacturer, "name"),
        "platforms": (Platform, "name"),
        "tenants": (Tenant, "name"),
        "namespaces": (Namespace, "name"),
        "location_types": (LocationType, "name"),
    }
    for family, (model, field_name) in lookups.items():
        for ref in spec.references.get(family, []):
            obj = model.objects.filter(**{field_name: ref[field_name]}).first()
            if obj is None:
                _fail(f"{model.__name__} {ref[field_name]!r} referenced by the template does not exist")
            for label in ref.get("content_types", []):
                app_label, model_name = label.split(".")
                if hasattr(obj, "content_types") and not obj.content_types.filter(
                    app_label=app_label, model=model_name
                ).exists():
                    _fail(
                        f"{model.__name__} {ref[field_name]!r} is not enabled for "
                        f"content type {label}"
                    )

    for ref in spec.references.get("device_types", []):
        device_type = DeviceType.objects.filter(
            model=ref["model"], manufacturer__name=ref["manufacturer"]
        ).first()
        if device_type is None:
            _fail(f"DeviceType {ref['manufacturer']}/{ref['model']} does not exist")
        if ref.get("template_fingerprint"):
            if fingerprint_device_type(device_type) != ref["template_fingerprint"]:
                _fail(
                    f"DeviceType {ref['manufacturer']}/{ref['model']} component "
                    "templates changed since the template was blessed (fingerprint "
                    "mismatch) — re-capture and re-bless"
                )


def _check_custom_fields(spec: SiteSpec) -> None:
    from nautobot.extras.models import CustomField

    keys = [ref["key"] for ref in spec.references.get("custom_fields", [])]
    keys.append(PROVISIONED_FROM_FIELD)
    for key in keys:
        if not CustomField.objects.filter(key=key).exists():
            _fail(
                f"Custom field {key!r} must exist before deploying "
                "(see the lab runbook for setup)"
            )


def _check_parent_location(spec: SiteSpec, seeds: Seeds) -> None:
    from nautobot.dcim.models import Location

    parent = seeds.parent_location
    if parent is None:
        _fail("a parent location is required")
    expected = (spec.meta.get("source") or {}).get("parent_location_type") or ""
    if expected and parent.location_type.name != expected:
        _fail(
            f"parent location {parent} has type {parent.location_type.name}; "
            f"this template expects {expected}"
        )
    if Location.objects.filter(parent=parent, name=seeds.site_name).exists():
        _fail(f"a location named {seeds.site_name!r} already exists under {parent}")


def _check_name_collisions(resolved_plan: dict) -> None:
    """Resolved names are exact — check them all (stronger than the retired
    codegen, which could only check simple substitutions)."""
    from nautobot.dcim.models import Device, Rack, RackGroup
    from nautobot.ipam.models import VLANGroup

    for family, model in (
        ("devices", Device),
        ("racks", Rack),
        ("rack_groups", RackGroup),
    ):
        for obj in resolved_plan.get(family, []):
            if model.objects.filter(name=obj["name"]).exists():
                _fail(
                    f"{model.__name__} named {obj['name']!r} already exists — "
                    "deploying would collide (wrong site code, or already deployed?)"
                )
    for group in resolved_plan.get("vlan_groups", []):
        if VLANGroup.objects.filter(name=group["name"]).exists():
            _fail(f"VLANGroup {group['name']!r} already exists")


def _check_supernets(pmap: ParamMap, seeds: Seeds) -> None:
    from nautobot.ipam.models import Prefix

    for entry in pmap.supernets:
        source = ipaddress.ip_network(entry["source"])
        try:
            target = ipaddress.ip_network(str(seeds.supernets[entry["seed"]]))
        except ValueError as err:
            _fail(f"seed {entry['seed']}: {err}")
            return
        if target.version != source.version:
            _fail(f"seed {entry['seed']} must be IPv{source.version} (replaces {source})")
        if target.prefixlen > source.prefixlen:
            _fail(
                f"seed {entry['seed']} ({target}) is smaller than the source "
                f"supernet {source} it replaces"
            )
        # Overlap scan in Python: avoids version-specific ORM net filters.
        for network, prefix_length in Prefix.objects.all().values_list(
            "network", "prefix_length"
        ):
            existing = ipaddress.ip_network(f"{network}/{prefix_length}", strict=False)
            if existing.version == target.version and target.overlaps(existing):
                _fail(
                    f"supernet {target} for seed {entry['seed']} overlaps existing "
                    f"prefix {existing} — choose unallocated space"
                )

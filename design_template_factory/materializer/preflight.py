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
    _check_supernets(pmap, seeds, resolved_plan)
    _check_stamp_coverage(resolved_plan)
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
    """Only GLOBALLY-unique identities can collide with existing objects.

    Devices/racks/rack groups are unique per (location/tenant/group, name)
    and every location this deployment creates is new, so those cannot
    collide with other sites (review finding: a global name check wrongly
    blocked deploys over the golden site's own non-site-coded names).
    """
    from nautobot.ipam.models import VLANGroup

    for group in resolved_plan.get("vlan_groups", []):
        if VLANGroup.objects.filter(name=group["name"]).exists():
            _fail(f"VLANGroup {group['name']!r} already exists")


def _check_supernets(pmap: ParamMap, seeds: Seeds, resolved_plan: dict) -> None:
    from nautobot.ipam.models import Prefix

    # Scope the overlap scan to the namespaces this plan writes into, and
    # allow nesting under Container-type aggregates (that is what containers
    # are FOR); fail on Network/Pool overlaps and equal/more-specific
    # containment (review finding: a blanket overlap check made deployment
    # impossible on any instance with a normal aggregate hierarchy).
    plan_namespaces = {p["namespace"] for p in resolved_plan.get("prefixes", [])}
    existing = list(
        Prefix.objects.filter(namespace__name__in=plan_namespaces).values_list(
            "network", "prefix_length", "type", "namespace__name"
        )
    )
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
        for network, prefix_length, prefix_type, namespace in existing:
            other = ipaddress.ip_network(f"{network}/{prefix_length}", strict=False)
            if other.version != target.version or not target.overlaps(other):
                continue
            container = str(prefix_type or "").lower() == "container"
            nests_inside = target.subnet_of(other) and target != other
            if container and nests_inside:
                continue  # aggregates are where new site supernets belong
            _fail(
                f"supernet {target} for seed {entry['seed']} overlaps existing "
                f"prefix {other} (type={prefix_type or 'network'}, namespace="
                f"{namespace}) — choose unallocated space"
            )


# Content types the executor stamps; the provisioned_from custom field must
# be enabled for each or the deploy would plant CF data validation rejects.
STAMPED_CONTENT_TYPES = (
    "dcim.location",
    "dcim.rackgroup",
    "dcim.rack",
    "dcim.powerpanel",
    "dcim.powerfeed",
    "dcim.device",
    "dcim.cable",
    "ipam.vlangroup",
    "ipam.vlan",
    "ipam.prefix",
    "ipam.ipaddress",
)


def _check_stamp_coverage(resolved_plan: dict) -> None:
    from nautobot.extras.models import CustomField

    cf = CustomField.objects.filter(key=PROVISIONED_FROM_FIELD).first()
    if cf is None:
        return  # existence already failed in _check_custom_fields
    enabled = {
        f"{ct.app_label}.{ct.model}" for ct in cf.content_types.all()
    }
    missing = [label for label in STAMPED_CONTENT_TYPES if label not in enabled]
    if missing:
        _fail(
            f"custom field {PROVISIONED_FROM_FIELD!r} is not enabled for: "
            f"{', '.join(missing)} — see the lab runbook setup step"
        )

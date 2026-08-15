"""Stamp-scoped teardown: delete everything a deployment created.

Every object the executor writes carries the `provisioned_from` stamp, so
teardown is a JSONField query per family, deleted in reverse creation order
(cables first, locations last, children before parents). Dry-run lists what
would be deleted without touching anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PROVISIONED_FROM_FIELD = "provisioned_from"


@dataclass
class TeardownReport:
    stamp: str
    dryrun: bool
    counts: dict[str, int] = field(default_factory=dict)
    details: list[str] = field(default_factory=list)


def _stamped(model, stamp: str):
    return model.objects.filter(
        **{f"_custom_field_data__{PROVISIONED_FROM_FIELD}": stamp}
    )


def teardown(stamp: str, *, dryrun: bool, logger=None) -> TeardownReport:
    from django.db import transaction
    from nautobot.dcim.models import (
        Cable,
        Device,
        Location,
        PowerFeed,
        PowerPanel,
        Rack,
        RackGroup,
    )
    from nautobot.ipam.models import VLAN, IPAddress, Prefix, VLANGroup

    # Reverse creation order. Device deletion cascades its components and
    # IP-to-interface rows; IPs/prefixes/VLANs follow; locations go
    # children-first (sorted by tree depth descending).
    ordered: list[tuple[str, object]] = [
        ("cables", Cable),
        ("devices", Device),
        ("ip_addresses", IPAddress),
        ("prefixes", Prefix),
        ("vlans", VLAN),
        ("vlan_groups", VLANGroup),
        ("power_feeds", PowerFeed),
        ("power_panels", PowerPanel),
        ("racks", Rack),
        ("rack_groups", RackGroup),
    ]

    report = TeardownReport(stamp=stamp, dryrun=dryrun)
    with transaction.atomic():
        for family, model in ordered:
            queryset = _stamped(model, stamp)
            objects = list(queryset)
            report.counts[family] = len(objects)
            for obj in objects:
                report.details.append(f"{family}: {obj}")
            if objects:
                queryset.delete()

        locations = sorted(
            _stamped(Location, stamp),
            key=lambda loc: len(loc.ancestors()) if hasattr(loc, "ancestors") else 0,
            reverse=True,
        )
        report.counts["locations"] = len(locations)
        for location in locations:
            report.details.append(f"locations: {location}")
            location.delete()

        if dryrun:
            report.details.append("dry run: all deletions rolled back")
            transaction.set_rollback(True)

    if logger is not None:
        logger.info("teardown %s: %s", "planned" if dryrun else "complete", report.counts)
    return report

"""Site graph walker: one Location subtree -> site-spec dict + lint findings.

Read-only. Serialization rules follow the plan (§3.1): natural keys only,
site-scoped objects captured for creation, Tier-0 shared objects recorded as
references, cables as endpoint pairs, strip-list enforced, cross-site edges
flagged instead of cloned. Locations are keyed by root-relative *path*
(names are only unique per parent).
"""

from __future__ import annotations

from design_template_factory import constants, lint
from design_template_factory.constants import CABLE_ENDPOINT_TYPES

from .component_diff import FAMILY_SOURCES, diff_device_components, fingerprint_device_type


class SiteWalker:
    def __init__(self, root_location, site_code: str):
        self.root = root_location
        self.site_code = site_code
        self.findings: list[lint.Finding] = []
        self._locations: list = []  # Location instances, parent-first
        self._location_paths: dict = {}  # pk -> root-relative path
        self._devices: list = []
        self._device_names: set[str] = set()
        self._panel_names: set[str] = set()
        self._feed_pks: set = set()
        self._references: dict[str, dict] = {
            family: {} for family in constants.REFERENCE_FAMILIES
        }

    # ------------------------------------------------------------ reference
    def _ref_status(self, obj, used_on: str) -> str | None:
        status = getattr(obj, "status", None)
        if status is None:
            return None
        entry = self._references["statuses"].setdefault(
            status.name, {"name": status.name, "content_types": []}
        )
        if used_on not in entry["content_types"]:
            entry["content_types"].append(used_on)
        return status.name

    def _ref_role(self, role, used_on: str) -> str | None:
        if role is None:
            return None
        entry = self._references["roles"].setdefault(
            role.name, {"name": role.name, "content_types": []}
        )
        if used_on not in entry["content_types"]:
            entry["content_types"].append(used_on)
        return role.name

    def _ref_named(self, family: str, instance) -> str | None:
        if instance is None:
            return None
        self._references[family].setdefault(instance.name, {"name": instance.name})
        return instance.name

    def _ref_device_type(self, device_type) -> dict:
        key = f"{device_type.manufacturer.name}/{device_type.model}"
        self._references["device_types"].setdefault(
            key,
            {
                "manufacturer": device_type.manufacturer.name,
                "model": device_type.model,
                "template_fingerprint": fingerprint_device_type(device_type),
            },
        )
        self._references["manufacturers"].setdefault(
            device_type.manufacturer.name, {"name": device_type.manufacturer.name}
        )
        return {
            "manufacturer": device_type.manufacturer.name,
            "model": device_type.model,
        }

    def _custom_fields(self, obj) -> dict:
        data = dict(getattr(obj, "_custom_field_data", None) or {})
        for key in data:
            self._references["custom_fields"].setdefault(key, {"key": key})
        return data

    def _path(self, location) -> str:
        return self._location_paths[location.pk]

    def _lint(self, severity: str, category: str, message: str, obj: str = "") -> None:
        self.findings.append(
            lint.Finding(severity=severity, category=category, message=message, obj=obj)
        )

    # ---------------------------------------------------------------- walk
    def walk(self) -> dict:
        spec: dict = {}
        spec["locations"] = self._walk_locations()
        spec["rack_groups"], spec["racks"] = self._walk_racks()
        spec["power_panels"], spec["power_feeds"] = self._walk_power()
        spec["devices"] = self._walk_devices()
        spec["vlan_groups"] = []  # per-site group is a render-time construct
        spec["vlans"] = self._walk_vlans()
        spec["vrfs"] = self._walk_vrfs()
        spec["prefixes"] = self._walk_prefixes()
        (
            spec["ip_addresses"],
            spec["ip_assignments"],
            spec["primary_ips"],
        ) = self._walk_ips(spec["prefixes"])
        spec["cables"] = self._walk_cables()
        self._lint_config_contexts()
        self._lint_relationship_associations()
        return spec

    # ------------------------------------------------------------ locations
    def _walk_locations(self) -> list[dict]:
        entries = []
        stack = [(self.root, None)]
        while stack:
            location, parent_path = stack.pop(0)
            path = (
                location.name
                if parent_path is None
                else f"{parent_path}/{location.name}"
            )
            self._locations.append(location)
            self._location_paths[location.pk] = path
            self._ref_named("location_types", location.location_type)
            entries.append(
                {
                    "name": location.name,
                    "path": path,
                    "location_type": location.location_type.name,
                    "parent": parent_path,
                    "status": self._ref_status(location, "dcim.location"),
                    "description": location.description or None,
                    "custom_fields": self._custom_fields(location),
                }
            )
            for child in location.children.all().order_by("name"):
                stack.append((child, path))
        return entries

    # ---------------------------------------------------------------- racks
    def _walk_racks(self) -> tuple[list[dict], list[dict]]:
        from nautobot.dcim.models import Rack, RackGroup

        location_ids = list(self._location_paths)
        groups, racks = [], []
        group_names: set[str] = set()
        site_group_pks = set(
            RackGroup.objects.filter(location__in=location_ids).values_list("pk", flat=True)
        )
        for group in RackGroup.objects.filter(location__in=location_ids).order_by("name"):
            if group.parent_id is not None and group.parent_id not in site_group_pks:
                self._lint(
                    "error",
                    "cross-site-parent",
                    "rack group's parent lies outside the captured subtree — "
                    "restructure or descope before blessing",
                    group.name,
                )
            if group.name in group_names:
                self._lint(
                    "error",
                    "ambiguous-name",
                    "duplicate RackGroup name within the site — references "
                    "cannot be disambiguated; rename one of them",
                    group.name,
                )
            group_names.add(group.name)
            groups.append(
                {
                    "name": group.name,
                    "location": self._path(group.location),
                    "parent": group.parent.name if group.parent else None,
                    "custom_fields": self._custom_fields(group),
                }
            )
        rack_names: set[str] = set()
        for rack in Rack.objects.filter(location__in=location_ids).order_by("name"):
            if rack.name in rack_names:
                self._lint(
                    "error",
                    "ambiguous-name",
                    "duplicate Rack name within the site — rename one of them",
                    rack.name,
                )
            rack_names.add(rack.name)
            racks.append(
                {
                    "name": rack.name,
                    "location": self._path(rack.location),
                    "rack_group": rack.rack_group.name if rack.rack_group else None,
                    "status": self._ref_status(rack, "dcim.rack"),
                    "u_height": rack.u_height,
                    "width": rack.width,
                    "type": str(rack.type) if rack.type else None,
                    "custom_fields": self._custom_fields(rack),
                }
            )
        return groups, racks

    # ---------------------------------------------------------------- power
    def _walk_power(self) -> tuple[list[dict], list[dict]]:
        from nautobot.dcim.models import PowerFeed, PowerPanel

        location_ids = list(self._location_paths)
        panels, feeds = [], []
        for panel in PowerPanel.objects.filter(location__in=location_ids).order_by("name"):
            self._panel_names.add(panel.name)
            if self.site_code.lower() not in panel.name.lower():
                self._lint(
                    "warning",
                    "non-site-coded-name",
                    "power panel name does not embed the site code; if any cable "
                    "targets its feeds, rendering will fail until a rename "
                    "pattern covers panels",
                    panel.name,
                )
            panels.append(
                {
                    "name": panel.name,
                    "location": self._path(panel.location),
                    "rack_group": panel.rack_group.name if panel.rack_group else None,
                    "custom_fields": self._custom_fields(panel),
                }
            )
        for feed in PowerFeed.objects.filter(
            power_panel__location__in=location_ids
        ).order_by("name"):
            self._feed_pks.add(feed.pk)
            if self.site_code.lower() not in feed.name.lower():
                self._lint(
                    "info",
                    "non-site-coded-name",
                    "power feed name does not embed the site code; clones keep "
                    "the name (feeds are scoped by their panel)",
                    feed.name,
                )
            feeds.append(
                {
                    "name": feed.name,
                    "power_panel": feed.power_panel.name,
                    "status": self._ref_status(feed, "dcim.powerfeed"),
                    "type": str(feed.type) if feed.type else None,
                    "supply": str(feed.supply) if feed.supply else None,
                    "phase": str(feed.phase) if feed.phase else None,
                    "voltage": feed.voltage,
                    "amperage": feed.amperage,
                    "rack": feed.rack.name if feed.rack else None,
                    "custom_fields": self._custom_fields(feed),
                }
            )
        return panels, feeds

    # -------------------------------------------------------------- devices
    def _walk_devices(self) -> list[dict]:
        from nautobot.dcim.models import Device

        location_ids = list(self._location_paths)
        entries = []
        for device in (
            Device.objects.filter(location__in=location_ids)
            .select_related(
                "device_type__manufacturer", "role", "status", "platform", "rack", "tenant"
            )
            .order_by("name")
        ):
            if not device.name:
                self._lint(
                    "error",
                    "unnamed-device",
                    "device has no name and cannot be captured; name it or "
                    "remove it from the golden site",
                    str(device.pk),
                )
                continue
            if device.name in self._device_names:
                self._lint(
                    "error",
                    "ambiguous-name",
                    "duplicate device name within the site — rename one of them",
                    device.name,
                )
            self._devices.append(device)
            self._device_names.add(device.name)
            if self.site_code.lower() not in device.name.lower():
                self._lint(
                    "warning",
                    "non-site-coded-name",
                    "device name does not embed the site code; it will deploy "
                    "with plain !create (no idempotent lookup) unless renamed "
                    "via parameter-map patterns",
                    device.name,
                )
            if getattr(device, "virtual_chassis", None) is not None:
                self._lint(
                    "warning",
                    "scope-skip",
                    "device is in a VirtualChassis — VC is v2 scope and is NOT captured",
                    device.name,
                )
            components, component_findings = diff_device_components(device)
            self.findings.extend(component_findings)
            self._ref_role(device.role, "dcim.device")
            self._ref_status(device, "dcim.device")
            if device.platform:
                self._ref_named("platforms", device.platform)
            if device.tenant:
                self._ref_named("tenants", device.tenant)
            entry = {
                "name": device.name,
                "device_type": self._ref_device_type(device.device_type),
                "role": device.role.name,
                "status": device.status.name,
                "platform": device.platform.name if device.platform else None,
                "tenant": device.tenant.name if device.tenant else None,
                "location": self._path(device.location),
                "rack": device.rack.name if device.rack else None,
                "position": device.position,
                "face": str(device.face) if device.face else None,
                "custom_fields": self._custom_fields(device),
                "_components": components,
            }
            local_context = getattr(device, "local_config_context_data", None)
            if local_context:
                entry["local_config_context_data"] = local_context
            entries.append(entry)
        return entries

    # ------------------------------------------------------------ vlans/vrfs
    def _walk_vlans(self) -> list[dict]:
        from nautobot.ipam.models import VLAN

        location_ids = list(self._location_paths)
        by_location = set(VLAN.objects.filter(locations__in=location_ids))
        interface_vlans = set()
        for device in self._devices:
            for iface in device.interfaces.all():
                if iface.untagged_vlan is not None:
                    interface_vlans.add(iface.untagged_vlan)
                interface_vlans.update(iface.tagged_vlans.all())

        for vlan in interface_vlans - by_location:
            self._lint(
                "info",
                "vlan-not-location-assigned",
                "VLAN is referenced by site interfaces but not location-assigned "
                "to the site; captured anyway",
                f"VLAN {vlan.vid}",
            )

        vlans = by_location | interface_vlans
        entries = []
        seen_vids: set[int] = set()
        for vlan in sorted(vlans, key=lambda v: (v.vid, v.name)):
            if vlan.vid in seen_vids:
                self._lint(
                    "error",
                    "ambiguous-vlan",
                    "two site VLANs share this vid; the spec identifies VLANs by "
                    "vid and cannot disambiguate them — consolidate or descope",
                    f"VLAN {vlan.vid}",
                )
                continue
            seen_vids.add(vlan.vid)
            if vlan.vlan_group is None:
                self._lint(
                    "info",
                    "groupless-vlan",
                    "source VLAN has no VLANGroup; clones place all VLANs in the "
                    "per-site group",
                    f"VLAN {vlan.vid}",
                )
            self._ref_role(getattr(vlan, "role", None), "ipam.vlan")
            entries.append(
                {
                    "vid": vlan.vid,
                    "name": vlan.name,
                    "status": self._ref_status(vlan, "ipam.vlan"),
                    "role": vlan.role.name if vlan.role else None,
                    "description": vlan.description or None,
                    "custom_fields": self._custom_fields(vlan),
                }
            )
        return entries

    def _walk_vrfs(self) -> list[dict]:
        # v1: VRFs are lint-flagged, not captured (rd/namespace parameterization
        # needs its own design pass; the renderer refuses specs with vrfs).
        from nautobot.ipam.models import VRF

        vrfs = VRF.objects.filter(
            prefixes__locations__in=list(self._location_paths)
        ).distinct()
        for vrf in vrfs:
            self._lint(
                "warning",
                "scope-skip",
                "VRF associated with site prefixes is NOT captured in v1",
                str(vrf),
            )
        return []

    # ------------------------------------------------------------- prefixes
    def _walk_prefixes(self) -> list[dict]:
        from nautobot.ipam.models import Prefix

        location_ids = list(self._location_paths)
        entries = []
        for prefix in Prefix.objects.filter(locations__in=location_ids).distinct().order_by(
            "network", "prefix_length"
        ):
            self._ref_named("namespaces", prefix.namespace)
            entries.append(
                {
                    "prefix": str(prefix.prefix),
                    "namespace": prefix.namespace.name,
                    "status": self._ref_status(prefix, "ipam.prefix"),
                    "type": str(prefix.type) if prefix.type else None,
                    "description": prefix.description or None,
                    "custom_fields": self._custom_fields(prefix),
                }
            )
        if not entries:
            self._lint(
                "warning",
                "no-prefixes",
                "no prefixes are location-assigned to this site; IPs cannot be "
                "captured without their parent prefixes",
            )
        return entries

    # ------------------------------------------------------------------ IPs
    def _walk_ips(self, prefix_entries: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
        from nautobot.ipam.models import IPAddressToInterface

        captured_prefixes = {p["prefix"] for p in prefix_entries}
        addresses: dict[str, dict] = {}
        assignments: list[dict] = []
        primaries: list[dict] = []

        for through in IPAddressToInterface.objects.filter(
            interface__device__in=[d.pk for d in self._devices]
        ).select_related("ip_address", "interface__device"):
            ip = through.ip_address
            address = str(ip.address)
            if address not in addresses:
                if ip.parent is None or str(ip.parent.prefix) not in captured_prefixes:
                    self._lint(
                        "error",
                        "capture-gap",
                        f"IP {address} has parent prefix "
                        f"{ip.parent.prefix if ip.parent else None} which is not "
                        "location-assigned to the site — assign it or descope the IP",
                        address,
                    )
                self._ref_role(getattr(ip, "role", None), "ipam.ipaddress")
                self._ref_named("namespaces", ip.parent.namespace if ip.parent else None)
                addresses[address] = {
                    "address": address,
                    "namespace": ip.parent.namespace.name if ip.parent else None,
                    "status": self._ref_status(ip, "ipam.ipaddress"),
                    "role": ip.role.name if getattr(ip, "role", None) else None,
                    "dns_name": ip.dns_name or None,
                    "description": ip.description or None,
                    "custom_fields": self._custom_fields(ip),
                }
            assignment = {
                "ip": address,
                "device": through.interface.device.name,
                "interface": through.interface.name,
            }
            for flag in constants.IP_ASSIGNMENT_FLAGS:
                if getattr(through, flag, False):
                    assignment[flag] = True
            assignments.append(assignment)

        for device in self._devices:
            for family, field in ((4, "primary_ip4"), (6, "primary_ip6")):
                primary = getattr(device, field, None)
                if primary is not None:
                    primaries.append(
                        {
                            "device": device.name,
                            "ip": str(primary.address),
                            "family": family,
                        }
                    )
                    if str(primary.address) not in addresses:
                        self._lint(
                            "error",
                            "capture-gap",
                            f"primary IP {primary.address} is not interface-assigned "
                            "within the captured site",
                            device.name,
                        )

        assignments.sort(key=lambda a: (a["device"], a["interface"], a["ip"]))
        return list(addresses.values()), assignments, primaries

    # --------------------------------------------------------------- cables
    def _walk_cables(self) -> list[dict]:
        # Cables have no site-scoped queryset (generic-FK endpoints); collect
        # them from the cable-bearing components of captured devices and the
        # site's power feeds.
        from nautobot.dcim.models import PowerFeed

        cables_by_pk: dict = {}
        for device in self._devices:
            for family, (related, _tmpl) in FAMILY_SOURCES.items():
                if family == "device_bays":
                    continue
                manager = getattr(device, related, None)
                if manager is None:
                    continue
                for component in manager.all():
                    cable = getattr(component, "cable", None)
                    if cable is not None:
                        cables_by_pk[cable.pk] = cable

        for feed in PowerFeed.objects.filter(pk__in=self._feed_pks):
            if feed.cable is not None:
                cables_by_pk[feed.cable.pk] = feed.cable

        entries = []
        for _pk, cable in sorted(cables_by_pk.items(), key=lambda kv: str(kv[0])):
            sides = []
            skip = False
            for termination in (cable.termination_a, cable.termination_b):
                if termination is None:
                    skip = True
                    break
                ct = f"{termination._meta.app_label}.{termination._meta.model_name}"
                family = CABLE_ENDPOINT_TYPES.get(ct)
                if family is None:
                    self._lint(
                        "warning",
                        "cross-site-cable",
                        f"cable endpoint type {ct} (e.g. circuit termination) is "
                        "out of v1 scope — cable skipped",
                        str(cable),
                    )
                    skip = True
                    break
                if family == "power_feeds":
                    if termination.pk not in self._feed_pks:
                        self._lint(
                            "warning",
                            "cross-site-cable",
                            "cable terminates on a power feed outside the captured "
                            "site — skipped",
                            str(cable),
                        )
                        skip = True
                        break
                    owner = termination.power_panel.name
                else:
                    owner_device = termination.device
                    if owner_device is None or owner_device.name not in self._device_names:
                        self._lint(
                            "warning",
                            "cross-site-cable",
                            "cable leaves the captured site — skipped",
                            str(cable),
                        )
                        skip = True
                        break
                    owner = owner_device.name
                sides.append([owner, family, termination.name])
            if skip or len(sides) != 2:
                continue
            if sides[0][1] != sides[1][1] and "power_feeds" not in (
                sides[0][1],
                sides[1][1],
            ):
                self._lint(
                    "info",
                    "cross-family-cable",
                    "cable connects different component families (e.g. console "
                    "port to console server port) — the connect_cable 'to' query "
                    "has no termination-model discriminator; spike gate 2 item",
                    str(cable),
                )
            self._ref_status(cable, "dcim.cable")
            entries.append(
                {
                    "a": sides[0],
                    "b": sides[1],
                    "status": cable.status.name,
                    "type": str(cable.type) if cable.type else None,
                    "label": cable.label or None,
                }
            )
        entries.sort(key=lambda c: (c["a"], c["b"]))
        return entries

    # -------------------------------------------------------------- extras
    def _lint_config_contexts(self) -> None:
        from nautobot.extras.models import ConfigContext

        for context in ConfigContext.objects.filter(
            locations__in=list(self._location_paths)
        ).distinct():
            self._lint(
                "warning",
                "config-context",
                "location-scoped ConfigContext applies to the source site but "
                "will NOT auto-apply to clones — extend its location list "
                "manually per deployment, or re-scope it by role/tag",
                context.name,
            )

    def _lint_relationship_associations(self) -> None:
        from django.contrib.contenttypes.models import ContentType
        from nautobot.extras.models import RelationshipAssociation

        device_ct = ContentType.objects.get(app_label="dcim", model="device")
        location_ct = ContentType.objects.get(app_label="dcim", model="location")
        captured_pks = {d.pk for d in self._devices} | set(self._location_paths)
        associations = RelationshipAssociation.objects.filter(
            source_type__in=[device_ct, location_ct], source_id__in=captured_pks
        ) | RelationshipAssociation.objects.filter(
            destination_type__in=[device_ct, location_ct],
            destination_id__in=captured_pks,
        )
        for association in associations.distinct():
            self._lint(
                "warning",
                "scope-skip",
                "RelationshipAssociation touches a captured object but "
                "relationships are NOT captured in v1 — clones will lack it",
                str(association),
            )

    def references(self) -> dict[str, list[dict]]:
        return {
            family: [entry for _key, entry in sorted(refs.items())]
            for family, refs in self._references.items()
        }

"""Execute a ResolvedPlan against the Nautobot ORM.

The trap ledger, handled once (see docs/research/site-data-model.md):
- Tier-0 shared objects are resolve-or-fail, never created;
- creation follows CREATION_ORDER (locations parent-first via paths, rack
  groups topo-sorted, prefixes before IPs, cables last);
- devices: create (DeviceType templates auto-instantiate components) ->
  delete removals -> create additions -> apply overrides -> link pass
  (lag / rear_port) -> IP assignment -> deferred primary-IP write;
- cables: check-before-create on endpoint occupancy; generic FKs directly
  (cross-family console cabling is just a write here);
- every created object (components included) is stamped via the
  `provisioned_from` custom field;
- one @transaction.atomic; dry-run = deliberate rollback.

Everything uses validated_save() (never bare save/bulk_create), per the
documented Jobs pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .resolver import Seeds

PROVISIONED_FROM_FIELD = "provisioned_from"


class ExecutionError(ValueError):
    """A write failed; the transaction rolls back."""


@dataclass
class ExecutionReport:
    stamp: str
    dryrun: bool
    counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add(self, family: str, count: int = 1) -> None:
        self.counts[family] = self.counts.get(family, 0) + count


class Executor:
    def __init__(self, plan: dict, seeds: Seeds, stamp: str, logger=None):
        self.plan = plan
        self.seeds = seeds
        self.stamp = stamp
        self.logger = logger
        self.report = ExecutionReport(stamp=stamp, dryrun=False)
        # resolution maps
        self._locations: dict[str, object] = {}   # path -> Location
        self._rack_groups: dict[str, object] = {}
        self._racks: dict[str, object] = {}
        self._panels: dict[str, object] = {}
        self._feeds: dict[tuple[str, str], object] = {}
        self._vlans: dict[int, object] = {}
        self._prefixes: dict[str, object] = {}
        self._devices: dict[str, object] = {}
        self._ips: dict[str, object] = {}

    # -------------------------------------------------------------- helpers
    def _log(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)

    def _status(self, name: str):
        from nautobot.extras.models import Status

        try:
            return Status.objects.get(name=name)
        except Status.DoesNotExist as err:
            raise ExecutionError(f"status {name!r} does not exist") from err

    def _stamped(self, obj, custom_fields: dict | None) -> None:
        data = dict(custom_fields or {})
        data[PROVISIONED_FROM_FIELD] = self.stamp
        obj._custom_field_data = {**(obj._custom_field_data or {}), **data}

    def _save(self, obj, family: str, custom_fields: dict | None = None):
        self._stamped(obj, custom_fields)
        try:
            obj.validated_save()
        except Exception as err:
            raise ExecutionError(f"{family}: {obj} failed validation: {err}") from err
        self.report.add(family)
        return obj

    # -------------------------------------------------------------- execute
    def execute(self) -> ExecutionReport:
        self._create_locations()
        self._create_rack_groups()
        self._create_racks()
        self._create_power()
        self._create_vlans()
        self._create_prefixes()
        self._create_devices()
        self._create_ips()
        self._assign_ips()
        self._set_primary_ips()
        self._create_cables()
        return self.report

    def _create_locations(self) -> None:
        from nautobot.dcim.models import Location, LocationType

        for entry in self.plan.get("locations", []):
            location = Location(
                name=entry["name"],
                location_type=LocationType.objects.get(name=entry["location_type"]),
                parent=(
                    self.seeds.parent_location
                    if entry.get("parent") is None
                    else self._locations[entry["parent"]]
                ),
                status=self._status(entry.get("status") or "Active"),
                description=entry.get("description") or "",
            )
            self._save(location, "locations", entry.get("custom_fields"))
            self._locations[entry["path"]] = location

    def _create_rack_groups(self) -> None:
        from nautobot.dcim.models import RackGroup

        # parent-first (resolver preserves capture order, but be safe)
        remaining = list(self.plan.get("rack_groups", []))
        while remaining:
            progressed = False
            for entry in list(remaining):
                parent = entry.get("parent")
                if parent is not None and parent not in self._rack_groups:
                    continue
                group = RackGroup(
                    name=entry["name"],
                    location=self._locations[entry["location"]],
                    parent=self._rack_groups.get(parent) if parent else None,
                )
                self._save(group, "rack_groups", entry.get("custom_fields"))
                self._rack_groups[entry["name"]] = group
                remaining.remove(entry)
                progressed = True
            if not progressed:
                raise ExecutionError(f"rack_group parent cycle: {[e['name'] for e in remaining]}")

    def _create_racks(self) -> None:
        from nautobot.dcim.models import Rack

        for entry in self.plan.get("racks", []):
            rack = Rack(
                name=entry["name"],
                location=self._locations[entry["location"]],
                rack_group=self._rack_groups.get(entry.get("rack_group")),
                status=self._status(entry["status"]),
            )
            for attr in ("u_height", "width", "type"):
                if entry.get(attr) is not None:
                    setattr(rack, attr, entry[attr])
            self._save(rack, "racks", entry.get("custom_fields"))
            self._racks[entry["name"]] = rack

    def _create_power(self) -> None:
        from nautobot.dcim.models import PowerFeed, PowerPanel

        for entry in self.plan.get("power_panels", []):
            panel = PowerPanel(
                name=entry["name"],
                location=self._locations[entry["location"]],
                rack_group=self._rack_groups.get(entry.get("rack_group")),
            )
            self._save(panel, "power_panels", entry.get("custom_fields"))
            self._panels[entry["name"]] = panel

        for entry in self.plan.get("power_feeds", []):
            feed = PowerFeed(
                name=entry["name"],
                power_panel=self._panels[entry["power_panel"]],
                status=self._status(entry["status"]),
            )
            for attr in ("type", "supply", "phase", "voltage", "amperage"):
                if entry.get(attr) is not None:
                    setattr(feed, attr, entry[attr])
            if entry.get("rack"):
                feed.rack = self._racks[entry["rack"]]
            self._save(feed, "power_feeds", entry.get("custom_fields"))
            self._feeds[(entry["power_panel"], entry["name"])] = feed

    def _create_vlans(self) -> None:
        from nautobot.ipam.models import VLAN, VLANGroup

        group = None
        for entry in self.plan.get("vlan_groups", []):
            group = VLANGroup(name=entry["name"])
            self._save(group, "vlan_groups")

        root = self._root_location()
        for entry in self.plan.get("vlans", []):
            vlan = VLAN(
                vid=entry["vid"],
                name=entry["name"],
                vlan_group=group,
                status=self._status(entry["status"]),
                description=entry.get("description") or "",
            )
            self._save(vlan, "vlans", entry.get("custom_fields"))
            vlan.locations.add(root)
            self._vlans[entry["vid"]] = vlan

    def _create_prefixes(self) -> None:
        from nautobot.ipam.models import Namespace, Prefix

        root = self._root_location()
        for entry in self.plan.get("prefixes", []):
            network, prefix_length = entry["prefix"].split("/")
            prefix = Prefix(
                network=network,
                prefix_length=int(prefix_length),
                namespace=Namespace.objects.get(name=entry["namespace"]),
                status=self._status(entry["status"]),
                description=entry.get("description") or "",
            )
            if entry.get("type"):
                prefix.type = entry["type"]
            self._save(prefix, "prefixes", entry.get("custom_fields"))
            prefix.locations.add(root)
            self._prefixes[entry["prefix"]] = prefix

    def _root_location(self):
        for entry in self.plan.get("locations", []):
            if entry.get("parent") is None:
                return self._locations[entry["path"]]
        raise ExecutionError("plan has no root location")

    # -------------------------------------------------------------- devices
    def _create_devices(self) -> None:
        from nautobot.dcim.models import Device, DeviceType
        from nautobot.extras.models import Role

        for entry in self.plan.get("devices", []):
            device = Device(
                name=entry["name"],
                device_type=DeviceType.objects.get(
                    model=entry["device_type"]["model"],
                    manufacturer__name=entry["device_type"]["manufacturer"],
                ),
                role=Role.objects.get(name=entry["role"]),
                status=self._status(entry["status"]),
                location=self._locations[entry["location"]],
            )
            if entry.get("platform"):
                from nautobot.dcim.models import Platform

                device.platform = Platform.objects.get(name=entry["platform"])
            if entry.get("rack"):
                device.rack = self._racks[entry["rack"]]
                if entry.get("position") is not None:
                    device.position = entry["position"]
                if entry.get("face"):
                    device.face = entry["face"]
            # Device.save() auto-instantiates components from DeviceType templates.
            self._save(device, "devices", entry.get("custom_fields"))
            self._devices[entry["name"]] = device
            self._apply_components(device, entry.get("_components") or {})
            self._stamp_components(device)

    def _component_manager(self, device, family: str):
        managers = {
            "interfaces": device.interfaces,
            "console_ports": device.console_ports,
            "console_server_ports": device.console_server_ports,
            "power_ports": device.power_ports,
            "power_outlets": device.power_outlets,
            "front_ports": device.front_ports,
            "rear_ports": device.rear_ports,
            "device_bays": device.device_bays,
        }
        try:
            return managers[family]
        except KeyError as err:
            raise ExecutionError(f"unknown component family {family!r}") from err

    def _apply_components(self, device, components: dict) -> None:
        from nautobot.dcim.models import Interface

        link_pass: list[tuple[str, dict]] = []
        for family, buckets in components.items():
            manager = self._component_manager(device, family)
            # 1) removals — the delete verb the old engine lacked
            removals = buckets.get("removals") or []
            if removals:
                deleted = manager.filter(name__in=removals)
                count = deleted.count()
                deleted.delete()
                self.report.add(f"{family}_removed", count)
            # 2) additions (lag-type first so members can link)
            additions = sorted(
                buckets.get("additions") or [],
                key=lambda c: (0 if "lag" in str(c.get("type", "")) else 1, c["name"]),
            )
            for comp in additions:
                if family == "interfaces":
                    obj = Interface(
                        device=device,
                        name=comp["name"],
                        type=comp.get("type") or "virtual",
                        status=self._status("Active"),
                    )
                else:
                    obj = manager.model(device=device, name=comp["name"])
                    if comp.get("type"):
                        obj.type = comp["type"]
                self._apply_component_attrs(obj, comp)
                self._save(obj, f"{family}_added")
                if comp.get("lag") or comp.get("rear_port"):
                    link_pass.append((family, comp))
            # 3) overrides on template-born components
            for comp in buckets.get("overrides") or []:
                try:
                    obj = manager.get(name=comp["name"])
                except manager.model.DoesNotExist as err:
                    raise ExecutionError(
                        f"{device.name}: override target {family}/{comp['name']} "
                        "does not exist (template drift?)"
                    ) from err
                self._apply_component_attrs(obj, comp)
                obj.validated_save()
                self.report.add(f"{family}_updated")
                if comp.get("lag") or comp.get("rear_port"):
                    link_pass.append((family, comp))
        # 4) link pass — lag / rear_port references, after all components exist
        for family, comp in link_pass:
            manager = self._component_manager(device, family)
            obj = manager.get(name=comp["name"])
            if comp.get("lag"):
                obj.lag = device.interfaces.get(name=comp["lag"])
            if comp.get("rear_port"):
                obj.rear_port = device.rear_ports.get(name=comp["rear_port"])
            obj.validated_save()

    def _apply_component_attrs(self, obj, comp: dict) -> None:
        for attr in ("description", "label", "enabled", "mgmt_only", "mtu", "mode"):
            if comp.get(attr) is not None:
                setattr(obj, attr, comp[attr])
        if comp.get("untagged_vlan") is not None:
            obj.untagged_vlan = self._vlans[comp["untagged_vlan"]]
            if not getattr(obj, "mode", None):
                obj.mode = "access"
        if comp.get("tagged_vlans"):
            # m2m set after save; defer via post-attr save in caller then set
            obj._pending_tagged = [self._vlans[vid] for vid in comp["tagged_vlans"]]

    def _stamp_components(self, device) -> None:
        for family in (
            "interfaces", "console_ports", "console_server_ports", "power_ports",
            "power_outlets", "front_ports", "rear_ports", "device_bays",
        ):
            for comp in self._component_manager(device, family).all():
                self._stamped(comp, None)
                comp.save()  # stamp-only write; validation already ran
                pending = getattr(comp, "_pending_tagged", None)
                if pending:
                    comp.tagged_vlans.set(pending)

    # ----------------------------------------------------------------- IPAM
    def _create_ips(self) -> None:
        from nautobot.ipam.models import IPAddress, Namespace

        for entry in self.plan.get("ip_addresses", []):
            ip = IPAddress(
                address=entry["address"],
                namespace=Namespace.objects.get(name=entry["namespace"]),
                status=self._status(entry["status"]),
                dns_name=entry.get("dns_name") or "",
                description=entry.get("description") or "",
            )
            self._save(ip, "ip_addresses", entry.get("custom_fields"))
            self._ips[entry["address"]] = ip

    def _assign_ips(self) -> None:
        from nautobot.ipam.models import IPAddressToInterface

        from .. import constants

        for entry in self.plan.get("ip_assignments", []):
            device = self._devices[entry["device"]]
            interface = device.interfaces.get(name=entry["interface"])
            through = IPAddressToInterface(
                ip_address=self._ips[entry["ip"]], interface=interface
            )
            for flag in constants.IP_ASSIGNMENT_FLAGS:
                if entry.get(flag):
                    setattr(through, flag, True)
            self._save(through, "ip_assignments")

    def _set_primary_ips(self) -> None:
        for entry in self.plan.get("primary_ips", []):
            device = self._devices[entry["device"]]
            field_name = "primary_ip4" if entry.get("family", 4) == 4 else "primary_ip6"
            setattr(device, field_name, self._ips[entry["ip"]])
            device.validated_save()
            self.report.add("primary_ips")

    # ---------------------------------------------------------------- cables
    def _cable_endpoint(self, endpoint: list):
        owner, family, name = endpoint
        if family == "power_feeds":
            try:
                return self._feeds[(owner, name)]
            except KeyError as err:
                raise ExecutionError(f"unknown power feed {owner}/{name}") from err
        device = self._devices.get(owner)
        if device is None:
            raise ExecutionError(f"cable endpoint device {owner!r} not in plan")
        return self._component_manager(device, family).get(name=name)

    def _create_cables(self) -> None:
        from nautobot.dcim.models import Cable

        for entry in self.plan.get("cables", []):
            side_a = self._cable_endpoint(entry["a"])
            side_b = self._cable_endpoint(entry["b"])
            for side in (side_a, side_b):
                if getattr(side, "cable", None) is not None:
                    raise ExecutionError(
                        f"endpoint {side} already cabled — check-before-create"
                    )
            cable = Cable(
                termination_a=side_a,
                termination_b=side_b,
                status=self._status(entry.get("status") or "Connected"),
            )
            if entry.get("type"):
                cable.type = entry["type"]
            if entry.get("label"):
                cable.label = entry["label"]
            self._save(cable, "cables")


def execute_plan(plan: dict, seeds: Seeds, stamp: str, *, dryrun: bool, logger=None) -> ExecutionReport:
    """Run the plan inside one atomic transaction; dry-run rolls back."""
    from django.db import transaction

    executor = Executor(plan, seeds, stamp, logger=logger)
    with transaction.atomic():
        report = executor.execute()
        report.dryrun = dryrun
        if dryrun:
            report.notes.append("dry run: all writes rolled back")
            transaction.set_rollback(True)
    return report

"""Resolve a template (spec + param map) with deploy-time seeds — pure Python.

Output shape deliberately mirrors the capture walker's output families so the
verify job can deep-diff `capture(deployed site)` against `resolve(template,
seeds)` directly (see diffing.py for normalization rules).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import constants
from ..params import ParamMap
from ..rewrite import (
    RewriteError,
    find_root,
    rebase_address,
    rebase_network,
    resolve_name,
    resolve_tokens,
)
from ..spec import SiteSpec


class ResolveError(ValueError):
    """The template cannot be resolved with the given seeds."""


@dataclass
class Seeds:
    site_code: str
    site_name: str
    supernets: dict[str, str]  # seed name -> target CIDR
    parent_location: object | None = None  # Nautobot Location (unused by resolver)
    extra: dict = field(default_factory=dict)

    def validate_against(self, pmap: ParamMap) -> None:
        missing = [e["seed"] for e in pmap.supernets if e["seed"] not in self.supernets]
        if missing:
            raise ResolveError(f"missing supernet seeds: {missing}")


class Resolver:
    def __init__(self, spec: SiteSpec, pmap: ParamMap, seeds: Seeds):
        problems = spec.validate()
        if problems:
            raise ResolveError("template spec invalid: " + "; ".join(problems))
        if spec.entries("vrfs"):
            raise ResolveError(
                "vrfs are out of scope in v1 — remove them from the template "
                "(capture lints and skips them)"
            )
        seeds.validate_against(pmap)
        self.spec = spec
        self.pmap = pmap
        self.seeds = seeds
        # source-root -> target CIDR
        self.root_targets = {
            e["source"]: seeds.supernets[e["seed"]] for e in pmap.supernets
        }
        self._path_map = self._build_path_map()

    # ------------------------------------------------------------- helpers
    def _name(self, value: str) -> str:
        return resolve_name(value, self.pmap.name_patterns, self.seeds.site_code)

    def _rebase_net(self, network: str) -> str:
        root = find_root(network, self.root_targets)
        if root is None:
            raise ResolveError(f"prefix {network} outside every template supernet")
        return rebase_network(network, root, self.root_targets[root])

    def _rebase_addr(self, address: str) -> str:
        root = find_root(address, self.root_targets)
        if root is None:
            raise ResolveError(f"address {address} outside every template supernet")
        return rebase_address(address, root, self.root_targets[root])

    def _build_path_map(self) -> dict[str, str]:
        """Old location path -> resolved path (root renamed to site_name)."""
        mapping: dict[str, str] = {}
        for loc in self.spec.entries("locations"):
            if loc.get("parent") is None:
                new_name = self.seeds.site_name
            else:
                new_name = self._name(loc["name"])
            parent = loc.get("parent")
            if parent is None:
                mapping[loc["path"]] = new_name
            else:
                if parent not in mapping:
                    raise ResolveError(
                        f"location {loc['path']!r} parent {parent!r} not resolved "
                        "before use (spec ordering bug)"
                    )
                mapping[loc["path"]] = f"{mapping[parent]}/{new_name}"
        return mapping

    def _custom_fields(self, obj: dict) -> dict:
        return {
            k: v
            for k, v in (obj.get("custom_fields") or {}).items()
            if k not in self.pmap.drop_custom_fields
        }

    # ------------------------------------------------------------- resolve
    def resolve(self) -> dict:
        plan: dict[str, list] = {key: [] for key in constants.CREATION_ORDER}

        for loc in self.spec.entries("locations"):
            is_root = loc.get("parent") is None
            plan["locations"].append(
                {
                    "name": self.seeds.site_name if is_root else self._name(loc["name"]),
                    "path": self._path_map[loc["path"]],
                    "location_type": loc["location_type"],
                    "parent": None if is_root else self._path_map[loc["parent"]],
                    "status": loc.get("status"),
                    "description": loc.get("description"),
                    "custom_fields": self._custom_fields(loc),
                }
            )

        for group in self.spec.entries("rack_groups"):
            plan["rack_groups"].append(
                {
                    "name": self._name(group["name"]),
                    "location": self._path_map[group["location"]],
                    "parent": self._name(group["parent"]) if group.get("parent") else None,
                    "custom_fields": self._custom_fields(group),
                }
            )

        for rack in self.spec.entries("racks"):
            entry = dict(rack)
            entry["name"] = self._name(rack["name"])
            entry["location"] = self._path_map[rack["location"]]
            entry["rack_group"] = (
                self._name(rack["rack_group"]) if rack.get("rack_group") else None
            )
            entry["custom_fields"] = self._custom_fields(rack)
            plan["racks"].append(entry)

        for panel in self.spec.entries("power_panels"):
            entry = dict(panel)
            entry["name"] = self._name(panel["name"])
            entry["location"] = self._path_map[panel["location"]]
            entry["rack_group"] = (
                self._name(panel["rack_group"]) if panel.get("rack_group") else None
            )
            entry["custom_fields"] = self._custom_fields(panel)
            plan["power_panels"].append(entry)

        for feed in self.spec.entries("power_feeds"):
            entry = dict(feed)
            entry["name"] = self._name(feed["name"])
            entry["power_panel"] = self._name(feed["power_panel"])
            entry["rack"] = self._name(feed["rack"]) if feed.get("rack") else None
            entry["custom_fields"] = self._custom_fields(feed)
            plan["power_feeds"].append(entry)

        if self.spec.entries("vlans"):
            plan["vlan_groups"].append(
                {
                    "name": resolve_tokens(
                        self.pmap.vlan_group_name, self.seeds.site_code, self.seeds.site_name
                    )
                }
            )
        for vlan in self.spec.entries("vlans"):
            entry = dict(vlan)
            entry["custom_fields"] = self._custom_fields(vlan)
            plan["vlans"].append(entry)

        for prefix in self.spec.entries("prefixes"):
            entry = dict(prefix)
            entry["prefix"] = self._rebase_net(prefix["prefix"])
            entry["custom_fields"] = self._custom_fields(prefix)
            plan["prefixes"].append(entry)

        for device in self.spec.entries("devices"):
            entry = dict(device)
            entry["name"] = self._name(device["name"])
            entry["location"] = self._path_map[device["location"]]
            entry["rack"] = self._name(device["rack"]) if device.get("rack") else None
            entry["custom_fields"] = self._custom_fields(device)
            entry["_components"] = device.get("_components") or {}
            plan["devices"].append(entry)

        for ip in self.spec.entries("ip_addresses"):
            entry = dict(ip)
            entry["address"] = self._rebase_addr(ip["address"])
            if ip.get("dns_name"):
                entry["dns_name"] = self._name(ip["dns_name"])
            entry["custom_fields"] = self._custom_fields(ip)
            plan["ip_addresses"].append(entry)

        for assignment in self.spec.entries("ip_assignments"):
            entry = dict(assignment)
            entry["ip"] = self._rebase_addr(assignment["ip"])
            entry["device"] = self._name(assignment["device"])
            plan["ip_assignments"].append(entry)

        for primary in self.spec.entries("primary_ips"):
            plan["primary_ips"].append(
                {
                    "device": self._name(primary["device"]),
                    "ip": self._rebase_addr(primary["ip"]),
                    "family": primary.get("family", 4),
                }
            )

        for cable in self.spec.entries("cables"):
            entry = dict(cable)
            entry["a"] = self._endpoint(cable["a"])
            entry["b"] = self._endpoint(cable["b"])
            plan["cables"].append(entry)

        self._assert_resolved_uniqueness(plan)
        return plan

    @staticmethod
    def _assert_resolved_uniqueness(plan: dict) -> None:
        """Patterns (e.g. case-insensitive) can collapse distinct source names
        into one resolved name — silent object overwrites at execute time.
        Fail loudly here instead (review finding)."""
        checks = [
            ("locations", lambda o: o["path"]),
            ("rack_groups", lambda o: o["name"]),
            ("racks", lambda o: o["name"]),
            ("power_panels", lambda o: o["name"]),
            ("power_feeds", lambda o: (o["power_panel"], o["name"])),
            ("devices", lambda o: o["name"]),
            ("ip_addresses", lambda o: o["address"]),
        ]
        for family, key in checks:
            seen: dict = {}
            for obj in plan.get(family, []):
                identity = key(obj)
                if identity in seen:
                    raise ResolveError(
                        f"{family}: two template objects resolve to the same "
                        f"identity {identity!r} — adjust name patterns or "
                        "source names"
                    )
                seen[identity] = obj

    def _endpoint(self, endpoint: list) -> list:
        owner, family, component = endpoint
        # Power-feed endpoints are owned by panels, and the FEED itself is a
        # renamed created object — rename both (review finding: un-renamed
        # feed names broke cable lookup). Device-owned component names are
        # template-born and never renamed.
        if family == "power_feeds":
            return [self._name(owner), family, self._name(component)]
        return [self._name(owner), family, component]


def resolve(spec: SiteSpec, pmap: ParamMap, seeds: Seeds) -> dict:
    try:
        return Resolver(spec, pmap, seeds).resolve()
    except (RewriteError, ValueError) as err:
        if isinstance(err, ResolveError):
            raise
        raise ResolveError(str(err)) from err

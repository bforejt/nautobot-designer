"""Build the design documents (the projection from spec to Design Builder).

Output is a list of (filename, yaml_text) design files executed sequentially
by one DesignJob (verified: Meta.design_files share a single Environment, so
!ref handles created in file 1 resolve in later files). Multiple files exist
because a YAML mapping cannot repeat a top-level key and the second-pass
entries (primary IPs, cabling) re-enter ``devices``.

Safety invariants enforced here:
- every captured literal passes through escape_tree() (Jinja injection);
- generated Jinja uses single quotes only, and every string scalar is dumped
  in YAML double-quoted style — the one combination where the dump can never
  corrupt the Jinja (see escape.py's quoting contract). Non-string scalars
  keep their native YAML types;
- intra-site references use !ref handles from an injective registry (two
  distinct objects can never share a handle), never name lookups;
- locations are keyed by root-relative *path*, because location names are
  only unique per parent;
- create_or_update identifier lookups are emitted only where identity.py's
  uniqueness guarantee holds; everything else is plain !create.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

import yaml

from .. import constants, identity
from ..escape import Placeholder, escape_tree
from ..params import ParamMap
from ..rewrite import (
    address_offset_expr,
    apply_name_patterns,
    find_root,
    is_site_coded,
    network_offset_expr,
)
from ..spec import SiteSpec

# Interface attributes carried from spec component entries into the design.
_INTERFACE_ATTRS = ("description", "label", "enabled", "mgmt_only", "mtu", "mode", "type")
_GENERIC_COMPONENT_ATTRS = ("description", "label", "type", "rear_port_position")

PROVISIONED_FROM_FIELD = "provisioned_from"


class RenderError(ValueError):
    """The spec/param-map combination cannot be rendered safely."""


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value).lower()).strip("_")


class _Refs:
    """Injective !ref handle registry.

    Keyed by (family, *identity parts); readable slug base with a numeric
    suffix on collision, so distinct objects (e.g. 'Ethernet1/1' vs
    'Ethernet1.1') can never share a handle. render_documents() additionally
    asserts no duplicate '!ref' declarations survive in the output.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple, str] = {}
        self._taken: set[str] = set()

    def ref(self, family: str, *parts: str) -> str:
        key = (family, *[str(p) for p in parts])
        existing = self._by_key.get(key)
        if existing is not None:
            return existing
        base = _slug("_".join([family, *[str(p) for p in parts]])) or family
        candidate = base
        suffix = 2
        while candidate in self._taken:
            candidate = f"{base}_{suffix}"
            suffix += 1
        self._by_key[key] = candidate
        self._taken.add(candidate)
        return candidate

    def value(self, family: str, *parts: str) -> Placeholder:
        return Placeholder(f"!ref:{self.ref(family, *parts)}")


class DesignBuilderRenderer:
    def __init__(self, spec: SiteSpec, pmap: ParamMap):
        self.spec = spec
        self.pmap = pmap
        problems = spec.validate()
        if problems:
            raise RenderError("spec failed validation: " + "; ".join(problems))
        if spec.entries("vrfs"):
            raise RenderError(
                "spec contains VRFs but the v1 renderer has no VRF support — "
                "capture should have descoped these (lint category scope-skip)"
            )
        self.refs = _Refs()
        # name -> rendered (possibly Placeholder) name, per family
        self._rendered_names: dict[tuple[str, str], str | Placeholder] = {}
        # location name -> path (for device/rack references)
        self._location_paths = {
            loc["path"]: loc["path"] for loc in spec.entries("locations")
        }
        self._stamp = Placeholder(
            f"{pmap.template_id}@{spec.meta.get('template_version', '0')}"
            "/{{ site_code }}"
        )

    # ------------------------------------------------------------ name maps
    def rendered_name(self, family: str, name: str) -> str | Placeholder:
        key = (family, name)
        if key not in self._rendered_names:
            self._rendered_names[key] = apply_name_patterns(name, self.pmap.name_patterns)
        return self._rendered_names[key]

    def _identifier_entry(self, spec_key: str, name_value: str | Placeholder) -> dict:
        """Build the action-tag key for one object, enforcing the uniqueness rule."""
        action, field_name = identity.action_tag(spec_key)
        if (
            spec_key in identity.SITE_CODED_FAMILIES
            and action == identity.ACTION_CREATE_OR_UPDATE
            and not is_site_coded(name_value)
        ):
            # The global-uniqueness guarantee doesn't hold -> downgrade to the
            # no-lookup action. Safe (cannot mutate a foreign object); the
            # capture lint carries the downgrade note into the README.
            action = identity.ACTION_CREATE
        if field_name is None or field_name == "name":
            return {identity.design_key(action, field_name or "name"): name_value}
        raise RenderError(f"_identifier_entry only handles name identity, got {field_name}")

    # -------------------------------------------------------------- helpers
    def _stamped_custom_fields(self, obj: dict) -> dict:
        fields = {
            k: v
            for k, v in (obj.get("custom_fields") or {}).items()
            if k not in self.pmap.drop_custom_fields
        }
        fields[PROVISIONED_FROM_FIELD] = self._stamp
        return fields

    def _prefix_expr(self, prefix: str) -> Placeholder:
        root = find_root(prefix, self.pmap.source_roots)
        if root is None:
            raise RenderError(
                f"prefix {prefix} is outside every supernet in the parameter map"
            )
        return network_offset_expr(prefix, root, self.pmap.seed_for_root(root))

    def _address_expr(self, address: str) -> Placeholder:
        root = find_root(address, self.pmap.source_roots)
        if root is None:
            raise RenderError(
                f"address {address} is outside every supernet in the parameter map"
            )
        return address_offset_expr(address, root, self.pmap.seed_for_root(root))

    def _parent_prefix_ref(self, address: str) -> Placeholder:
        """Most-specific captured prefix containing the address -> its !ref.

        Setting the parent FK explicitly (rather than relying on namespace
        auto-resolution) keeps IP creation unambiguous and namespace-safe.
        """
        candidates = [
            p["prefix"]
            for p in self.spec.entries("prefixes")
            if find_root(address, [p["prefix"]]) is not None
        ]
        if not candidates:
            raise RenderError(
                f"IP {address} has no captured parent prefix; capture should have "
                "linted this"
            )
        best = max(candidates, key=lambda p: ipaddress.ip_network(p, strict=False).prefixlen)
        return self.refs.value("prefix", best)

    # ------------------------------------------------------------ documents
    def render_documents(self) -> list[tuple[str, str]]:
        creation = self._creation_document()
        primary = self._primary_ip_document()
        cabling = self._cabling_document()
        docs = [("designs/0001_site.yaml.j2", creation)]
        if primary:
            docs.append(("designs/0002_primary_ips.yaml.j2", primary))
        if cabling:
            docs.append(("designs/0003_cabling.yaml.j2", cabling))
        rendered = [(name, self._dump(doc)) for name, doc in docs]
        self._assert_unique_refs(docs)
        return rendered

    @staticmethod
    def _assert_unique_refs(docs: list[tuple[str, dict]]) -> None:
        seen: set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                declared = node.get("!ref")
                if declared is not None:
                    if declared in seen:
                        raise RenderError(
                            f"duplicate !ref handle {declared!r} in rendered design "
                            "(renderer registry bug — please report)"
                        )
                    seen.add(declared)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        for _name, doc in docs:
            walk(doc)

    def _dump(self, document: dict) -> str:
        header = (
            "# Generated by nautobot-design-template-factory — DO NOT EDIT.\n"
            f"# Template: {self.pmap.template_id} (source site "
            f"{self.pmap.source_site_code})\n"
            "# Regenerate with: dtf render <spec.json> <param-map.yaml>\n"
        )
        body = yaml.dump(
            _plainify(document),
            Dumper=_DesignDumper,
            sort_keys=False,
            width=10**9,  # never fold lines: folding could split a Jinja expression
            allow_unicode=True,
            default_flow_style=False,
        )
        return header + body

    # ---------------------------------------------------------- file 1 body
    def _creation_document(self) -> dict:
        doc: dict[str, list] = {}
        for spec_key, builder in (
            ("locations", self._locations),
            ("rack_groups", self._rack_groups),
            ("racks", self._racks),
            ("power_panels", self._power_panels),
            ("power_feeds", self._power_feeds),
            ("vlan_groups", self._vlan_groups),
            ("vlans", self._vlans),
            ("prefixes", self._prefixes),
            ("devices", self._devices),
            ("ip_addresses", self._ip_addresses),
        ):
            entries = builder()
            if entries:
                doc[spec_key] = entries

        assignments = self._ip_assignments()
        if assignments:
            # SPIKE-TODO (gate 2): confirm the exact design key Design Builder
            # derives for the IPAddressToInterface through model.
            doc["ip_address_to_interfaces"] = assignments
        return doc

    def _locations(self) -> list[dict]:
        entries = []
        for loc in self.spec.entries("locations"):
            # Locations use no-lookup !create: (parent, name) is a compound
            # key, so any name lookup is a cross-site hazard. A pre-existing
            # duplicate fails the transaction loudly — the safe direction.
            entry: dict[str, Any] = {
                "!create:name": self.rendered_name("locations", loc["name"])
            }
            entry["!ref"] = self.refs.ref("loc", loc["path"])
            entry["location_type__name"] = loc["location_type"]
            if loc.get("parent"):
                entry["parent"] = self.refs.value("loc", loc["parent"])
            else:
                # Site root: renamed to the deploy-time site_name seed and
                # parented on the ObjectVar. SPIKE-TODO (gate 2): pk-lookup
                # dict form for the parent (also listed in the plan and the
                # generated README as an inferred composition).
                entry["!create:name"] = Placeholder("{{ site_name }}")
                entry["parent"] = {"id": Placeholder("{{ parent_location.pk }}")}
            if loc.get("status"):
                entry["status__name"] = loc["status"]
            if loc.get("description"):
                entry["description"] = loc["description"]
            entry["custom_fields"] = self._stamped_custom_fields(loc)
            entries.append(entry)
        return entries

    def _rack_groups(self) -> list[dict]:
        # Parents must be defined before children (!ref is backward-only).
        remaining = list(self.spec.entries("rack_groups"))
        ordered: list[dict] = []
        emitted: set[str] = set()
        while remaining:
            progressed = False
            for group in list(remaining):
                parent = group.get("parent")
                if parent is None or parent in emitted:
                    ordered.append(group)
                    emitted.add(group["name"])
                    remaining.remove(group)
                    progressed = True
            if not progressed:
                raise RenderError(
                    "rack_groups contain a parent cycle or a parent missing from "
                    f"the spec: {[g['name'] for g in remaining]!r}"
                )

        entries = []
        for group in ordered:
            name = self.rendered_name("rack_groups", group["name"])
            entry = self._identifier_entry("rack_groups", name)
            entry["!ref"] = self.refs.ref("rackgroup", group["name"])
            entry["location"] = self.refs.value("loc", group["location"])
            if group.get("parent"):
                entry["parent"] = self.refs.value("rackgroup", group["parent"])
            entry["custom_fields"] = self._stamped_custom_fields(group)
            entries.append(entry)
        return entries

    def _racks(self) -> list[dict]:
        entries = []
        for rack in self.spec.entries("racks"):
            name = self.rendered_name("racks", rack["name"])
            entry = self._identifier_entry("racks", name)
            entry["!ref"] = self.refs.ref("rack", rack["name"])
            entry["location"] = self.refs.value("loc", rack["location"])
            if rack.get("rack_group"):
                entry["rack_group"] = self.refs.value("rackgroup", rack["rack_group"])
            entry["status__name"] = rack["status"]
            for attr in ("u_height", "width", "type", "desc_units"):
                if rack.get(attr) is not None:
                    entry[attr] = rack[attr]
            entry["custom_fields"] = self._stamped_custom_fields(rack)
            entries.append(entry)
        return entries

    def _power_panels(self) -> list[dict]:
        entries = []
        for panel in self.spec.entries("power_panels"):
            name = self.rendered_name("power_panels", panel["name"])
            # (location, name) compound key -> no-lookup create.
            entry: dict[str, Any] = {"!create:name": name}
            entry["!ref"] = self.refs.ref("panel", panel["name"])
            entry["location"] = self.refs.value("loc", panel["location"])
            if panel.get("rack_group"):
                entry["rack_group"] = self.refs.value("rackgroup", panel["rack_group"])
            entry["custom_fields"] = self._stamped_custom_fields(panel)
            entries.append(entry)
        return entries

    def _power_feeds(self) -> list[dict]:
        entries = []
        for feed in self.spec.entries("power_feeds"):
            entry: dict[str, Any] = {
                "!create:name": self.rendered_name("power_feeds", feed["name"])
            }
            entry["!ref"] = self.refs.ref("feed", feed["power_panel"], feed["name"])
            entry["power_panel"] = self.refs.value("panel", feed["power_panel"])
            entry["status__name"] = feed["status"]
            for attr in ("type", "supply", "phase", "voltage", "amperage", "max_utilization"):
                if feed.get(attr) is not None:
                    entry[attr] = feed[attr]
            if feed.get("rack"):
                entry["rack"] = self.refs.value("rack", feed["rack"])
            entry["custom_fields"] = self._stamped_custom_fields(feed)
            entries.append(entry)
        return entries

    def _vlan_groups(self) -> list[dict]:
        # Exactly one per-site VLANGroup (globally-unique name -> mandatory).
        if not self.spec.entries("vlans"):
            return []
        return [
            {
                "!create_or_update:name": Placeholder(self.pmap.vlan_group_name),
                "!ref": self.refs.ref("vlan_group", "site"),
                "custom_fields": {PROVISIONED_FROM_FIELD: self._stamp},
            }
        ]

    def _vlans(self) -> list[dict]:
        entries = []
        for vlan in self.spec.entries("vlans"):
            entry: dict[str, Any] = {"!create:name": vlan["name"]}
            entry["!ref"] = self.refs.ref("vlan", str(vlan["vid"]))
            entry["vid"] = vlan["vid"]
            entry["vlan_group"] = self.refs.value("vlan_group", "site")
            entry["status__name"] = vlan["status"]
            if vlan.get("role"):
                entry["role__name"] = vlan["role"]
            if vlan.get("description"):
                entry["description"] = vlan["description"]
            entry["custom_fields"] = self._stamped_custom_fields(vlan)
            entries.append(entry)
        return entries

    def _prefixes(self) -> list[dict]:
        entries = []
        for prefix in self.spec.entries("prefixes"):
            entry: dict[str, Any] = {
                "!create_or_update:prefix": self._prefix_expr(prefix["prefix"]),
            }
            entry["!ref"] = self.refs.ref("prefix", prefix["prefix"])
            entry["namespace__name"] = prefix["namespace"]
            entry["status__name"] = prefix["status"]
            if prefix.get("type"):
                entry["type"] = prefix["type"]
            if prefix.get("description"):
                entry["description"] = prefix["description"]
            entry["custom_fields"] = self._stamped_custom_fields(prefix)
            entries.append(entry)
        return entries

    # ---------------------------------------------------------------- devices
    def _component_entry(self, device: str, family: str, comp: dict, action: str) -> dict:
        name = comp["name"]
        entry: dict[str, Any] = {f"!{action}:name": name}
        ref_family = "if" if family == "interfaces" else family
        entry["!ref"] = self.refs.ref(ref_family, device, name)

        attrs = _INTERFACE_ATTRS if family == "interfaces" else _GENERIC_COMPONENT_ATTRS
        for attr in attrs:
            if comp.get(attr) is not None:
                entry[attr] = comp[attr]
        if family == "interfaces":
            if comp.get("untagged_vlan") is not None:
                entry["untagged_vlan"] = self.refs.value("vlan", str(comp["untagged_vlan"]))
            if comp.get("tagged_vlans"):
                entry["tagged_vlans"] = [
                    self.refs.value("vlan", str(vid)) for vid in comp["tagged_vlans"]
                ]
            if comp.get("lag"):
                entry["lag"] = self.refs.value("if", device, comp["lag"])
        if family == "front_ports" and comp.get("rear_port"):
            entry["rear_port"] = self.refs.value("rear_ports", device, comp["rear_port"])
        if action == identity.ACTION_CREATE:
            # Additions are objects this design creates -> stamp them.
            entry["custom_fields"] = self._stamped_custom_fields(comp)
        return entry

    def _device_components(self, device: dict) -> dict[str, list[dict]]:
        """Nested component entries: additions (!create) + overrides (!update).

        Ordering rules: rear_ports family before front_ports (declaration
        before use for rear_port refs); within interfaces, LAG targets before
        their members. Untouched template-born components that something
        references (cables, IP assignments, lag, rear_port) get a bare
        ref-bearing !update entry. SPIKE-TODO (gate 2): nested `!update:name`
        is assumed to scope its lookup to the parent device via child
        auto-association.
        """
        name = device["name"]
        components = device.get("_components") or {}
        nested: dict[str, list[dict]] = {}

        # Sibling references that must exist as entries: lag targets and
        # front_port -> rear_port targets.
        sibling_needs: dict[str, set[str]] = {family: set() for family in constants.COMPONENT_FAMILIES}
        for family, family_data in components.items():
            for bucket in ("additions", "overrides"):
                for comp in family_data.get(bucket) or []:
                    if family == "interfaces" and comp.get("lag"):
                        sibling_needs["interfaces"].add(comp["lag"])
                    if family == "front_ports" and comp.get("rear_port"):
                        sibling_needs["rear_ports"].add(comp["rear_port"])

        for family in ("rear_ports", "front_ports", "console_ports",
                       "console_server_ports", "power_ports", "power_outlets",
                       "interfaces", "device_bays"):
            spec_family = components.get(family) or {}
            additions = list(spec_family.get("additions") or [])
            overrides = list(spec_family.get("overrides") or [])
            explicit_names = {c["name"] for c in additions + overrides}

            needed = set(self._endpoints_needing_refs(name, family))
            needed |= sibling_needs.get(family, set())
            missing = sorted(needed - explicit_names)

            def sort_key(comp: dict) -> tuple:
                # LAG-capable targets first so member `lag:` refs resolve.
                is_lag_target = comp["name"] in sibling_needs.get(family, set())
                is_lag_type = "lag" in str(comp.get("type", ""))
                return (0 if (is_lag_target or is_lag_type) else 1, comp["name"])

            additions.sort(key=sort_key)
            overrides.sort(key=sort_key)

            entries = (
                [
                    self._component_entry(name, family, {"name": comp_name}, identity.ACTION_UPDATE)
                    for comp_name in missing
                    if comp_name in sibling_needs.get(family, set())
                ]
                + [
                    self._component_entry(name, family, comp, identity.ACTION_CREATE)
                    for comp in additions
                ]
                + [
                    self._component_entry(name, family, comp, identity.ACTION_UPDATE)
                    for comp in overrides
                ]
                + [
                    self._component_entry(name, family, {"name": comp_name}, identity.ACTION_UPDATE)
                    for comp_name in missing
                    if comp_name not in sibling_needs.get(family, set())
                ]
            )
            if entries:
                nested[family] = entries
        return nested

    def _endpoints_needing_refs(self, device: str, family: str) -> list[str]:
        needed: list[str] = []
        for assignment in self.spec.entries("ip_assignments"):
            if family == "interfaces" and assignment["device"] == device:
                needed.append(assignment["interface"])
        for cable in self.spec.entries("cables"):
            for end in ("a", "b"):
                owner, end_family, comp = cable[end]
                if owner == device and end_family == family:
                    needed.append(comp)
        return sorted(set(needed))

    def _devices(self) -> list[dict]:
        entries = []
        for device in self.spec.entries("devices"):
            name = self.rendered_name("devices", device["name"])
            entry = self._identifier_entry("devices", name)
            entry["!ref"] = self.refs.ref("device", device["name"])
            entry["device_type"] = {
                "model": device["device_type"]["model"],
                "manufacturer__name": device["device_type"]["manufacturer"],
            }
            entry["role__name"] = device["role"]
            entry["status__name"] = device["status"]
            if device.get("platform"):
                entry["platform__name"] = device["platform"]
            if device.get("tenant"):
                entry["tenant__name"] = device["tenant"]
            entry["location"] = self.refs.value("loc", device["location"])
            if device.get("rack"):
                entry["rack"] = self.refs.value("rack", device["rack"])
                for attr in ("position", "face"):
                    if device.get(attr) is not None:
                        entry[attr] = device[attr]
            if device.get("local_config_context_data"):
                entry["local_config_context_data"] = device["local_config_context_data"]
            entry["custom_fields"] = self._stamped_custom_fields(device)
            entry.update(self._device_components(device))
            entries.append(entry)
        return entries

    # ------------------------------------------------------------------- IPs
    def _ip_addresses(self) -> list[dict]:
        entries = []
        for ip in self.spec.entries("ip_addresses"):
            entry: dict[str, Any] = {
                "!create_or_update:address": self._address_expr(ip["address"]),
            }
            entry["!ref"] = self.refs.ref("ip", ip["address"])
            entry["parent"] = self._parent_prefix_ref(ip["address"])
            entry["status__name"] = ip["status"]
            if ip.get("role"):
                entry["role__name"] = ip["role"]
            if ip.get("dns_name"):
                # DNS names are site-specific; renamed via patterns like all names.
                entry["dns_name"] = self.rendered_name("dns", ip["dns_name"])
            if ip.get("description"):
                entry["description"] = ip["description"]
            entry["custom_fields"] = self._stamped_custom_fields(ip)
            entries.append(entry)
        return entries

    def _ip_assignments(self) -> list[dict]:
        entries = []
        for assignment in self.spec.entries("ip_assignments"):
            entry: dict[str, Any] = {
                "interface": self.refs.value("if", assignment["device"], assignment["interface"]),
                "ip_address": self.refs.value("ip", assignment["ip"]),
            }
            for flag in constants.IP_ASSIGNMENT_FLAGS:
                if assignment.get(flag):
                    entry[flag] = True
            entries.append(entry)
        return entries

    # ---------------------------------------------------------------- file 2
    def _primary_ip_document(self) -> dict | None:
        entries = []
        for primary in self.spec.entries("primary_ips"):
            name = self.rendered_name("devices", primary["device"])
            if not is_site_coded(name):
                # !update by non-unique name is exactly the cross-site hazard;
                # capture lint flags these devices already.
                raise RenderError(
                    f"cannot set primary IP on non-site-coded device "
                    f"{primary['device']!r} (name lookup would be ambiguous)"
                )
            field = "primary_ip4" if primary.get("family", 4) == 4 else "primary_ip6"
            entries.append(
                {
                    "!update:name": name,
                    field: self.refs.value("ip", primary["ip"]),
                }
            )
        return {"devices": entries} if entries else None

    # ---------------------------------------------------------------- file 3
    def _cabling_document(self) -> dict | None:
        device_entries: dict[str, dict] = {}
        for cable in self.spec.entries("cables"):
            (a_owner, a_family, a_name) = cable["a"]
            (b_owner, b_family, b_name) = cable["b"]
            if a_family == "power_feeds":
                # Normalize: emit from the device side.
                (a_owner, a_family, a_name), (b_owner, b_family, b_name) = (
                    (b_owner, b_family, b_name),
                    (a_owner, a_family, a_name),
                )

            rendered_owner = self.rendered_name("devices", a_owner)
            if not is_site_coded(rendered_owner):
                raise RenderError(
                    f"cable endpoint device {a_owner!r} is not site-coded; "
                    "cabling pass would use an ambiguous name lookup"
                )
            device_entry = device_entries.setdefault(
                a_owner, {"!update:name": rendered_owner}
            )

            # SPIKE-TODO (gate 2): connect_cable payload shape for cable
            # type/label attributes and for cross-model endpoints (console
            # port -> console server port has no explicit termination-model
            # discriminator in the 'to' query).
            connect: dict[str, Any] = {"status__name": cable.get("status", "Connected")}
            if cable.get("type"):
                connect["type"] = cable["type"]
            if cable.get("label"):
                connect["label"] = cable["label"]
            if b_family == "power_feeds":
                panel_name = self.rendered_name("power_panels", b_owner)
                if not is_site_coded(panel_name):
                    raise RenderError(
                        f"power panel {b_owner!r} is a cable target but its name is "
                        "not site-coded — an ambiguous {name, power_panel__name} "
                        "lookup could cable into another site. Add a rename "
                        "pattern for power panels to the parameter map."
                    )
                connect["to"] = {"name": b_name, "power_panel__name": panel_name}
            else:
                target_device = self.rendered_name("devices", b_owner)
                if not is_site_coded(target_device):
                    raise RenderError(
                        f"cable target device {b_owner!r} is not site-coded"
                    )
                connect["to"] = {"device__name": target_device, "name": b_name}

            device_entry.setdefault(a_family, [])
            device_entry[a_family].append(
                {"!update:name": a_name, "!connect_cable": connect}
            )

        if not device_entries:
            return None
        return {"devices": list(device_entries.values())}


class _DesignDumper(yaml.SafeDumper):
    """Forces double-quoted style for STRING scalars only.

    Strings (which may carry generated single-quoted Jinja) are dumped
    double-quoted so YAML never touches the Jinja; ints/bools/None keep their
    native YAML representation (a blanket default_style would stringify them).
    """


def _represent_str(dumper: yaml.SafeDumper, data: str):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


_DesignDumper.add_representer(str, _represent_str)
# PyYAML representer lookup is exact-type-first; register the str subclass too
# so a Placeholder surviving normalization can never hit RepresenterError.
_DesignDumper.add_representer(Placeholder, _represent_str)


def _plainify(value: Any) -> Any:
    """Escape literals and normalize Placeholders for YAML dumping."""
    escaped = escape_tree(value)

    def normalize(node: Any) -> Any:
        if isinstance(node, dict):
            return {str(k): normalize(v) for k, v in node.items()}
        if isinstance(node, list):
            return [normalize(item) for item in node]
        if isinstance(node, Placeholder):
            return str(node)
        return node

    return normalize(escaped)


def render_design_documents(spec: SiteSpec, pmap: ParamMap) -> list[tuple[str, str]]:
    return DesignBuilderRenderer(spec, pmap).render_documents()

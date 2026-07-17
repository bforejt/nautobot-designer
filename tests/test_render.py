"""Renderer tests: structure, ordering, safety invariants, determinism.

The design documents are Jinja templates; tests render them with a stub of
Design Builder's environment (StrictUndefined + a real network_offset filter)
and yaml.safe_load the result — the same parse path Design Builder uses.
"""

import ipaddress
from types import SimpleNamespace

import jinja2
import pytest
import yaml

from design_template_factory.params import propose
from design_template_factory.render.design import RenderError, render_design_documents


def _network_offset(network, offset):
    net = ipaddress.ip_network(str(network))
    off = ipaddress.ip_interface(offset)
    base = int(net.network_address) + int(off.ip)
    return f"{ipaddress.ip_address(base)}/{off.network.prefixlen}"


class _FakeIPNetwork:
    """Mimics netaddr.IPNetwork: not a str, but str()-able (tests `| string`)."""

    def __init__(self, cidr):
        self.cidr = cidr

    def __str__(self):
        return self.cidr


CONTEXT = {
    "site_code": "AUS01",
    "site_name": "Austin Branch",
    "parent_location": SimpleNamespace(pk="11111111-2222-3333-4444-555555555555"),
    "supernet_1": _FakeIPNetwork("10.20.0.0/16"),
}


def _render_and_load(documents):
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["network_offset"] = _network_offset
    loaded = {}
    for name, text in documents:
        rendered = env.from_string(text).render(**CONTEXT)
        loaded[name] = yaml.safe_load(rendered)
    return loaded


@pytest.fixture()
def documents(spec, param_map):
    return render_design_documents(spec, param_map)


@pytest.fixture()
def loaded(documents):
    return _render_and_load(documents)


def test_three_documents_in_order(documents):
    names = [name for name, _ in documents]
    assert names == [
        "designs/0001_site.yaml.j2",
        "designs/0002_primary_ips.yaml.j2",
        "designs/0003_cabling.yaml.j2",
    ]


def test_creation_document_key_order(loaded):
    doc = loaded["designs/0001_site.yaml.j2"]
    keys = list(doc)
    assert keys.index("locations") < keys.index("racks") < keys.index("devices")
    assert keys.index("prefixes") < keys.index("ip_addresses")
    assert keys.index("vlan_groups") < keys.index("vlans")


def test_site_root_renamed_and_parented(loaded):
    root = loaded["designs/0001_site.yaml.j2"]["locations"][0]
    assert root["!create:name"] == "Austin Branch"
    assert root["parent"] == {"id": "11111111-2222-3333-4444-555555555555"}


def test_injection_literals_are_inert(documents, loaded):
    # StrictUndefined would have raised if {{ malicious }} executed; the
    # rendered value must carry the literal delimiters through.
    root = loaded["designs/0001_site.yaml.j2"]["locations"][0]
    assert root["description"] == "Golden site {{ malicious }} fixture"
    sw1 = _device(loaded, "AUS01-SW1")
    eth1 = [i for i in sw1["interfaces"] if i.get("!update:name") == "Ethernet1"][0]
    assert eth1["description"] == "uplink {{ malicious }} literal"


def _device(loaded, rendered_name):
    for device in loaded["designs/0001_site.yaml.j2"]["devices"]:
        for key in ("!create_or_update:name", "!create:name"):
            if device.get(key) == rendered_name:
                return device
    raise AssertionError(f"device {rendered_name} not found")


def test_site_coded_device_gets_idempotent_lookup(loaded):
    sw1 = _device(loaded, "AUS01-SW1")
    assert "!create_or_update:name" in sw1
    assert sw1["custom_fields"]["provisioned_from"] == "branch-small@0/AUS01"


def test_non_site_coded_rack_downgraded_to_create(loaded):
    racks = loaded["designs/0001_site.yaml.j2"]["racks"]
    r2 = [r for r in racks if "R2" in str(r.values())][0]
    assert "!create:name" in r2
    assert "!create_or_update:name" not in r2


def test_prefixes_and_ips_rebased_via_seed(loaded):
    prefixes = loaded["designs/0001_site.yaml.j2"]["prefixes"]
    assert prefixes[0]["!create_or_update:prefix"] == "10.20.0.0/24"
    assert prefixes[1]["!create_or_update:prefix"] == "10.20.10.0/24"
    ips = loaded["designs/0001_site.yaml.j2"]["ip_addresses"]
    assert ips[0]["!create_or_update:address"] == "10.20.0.11/24"
    assert ips[0]["parent"] == "!ref:prefix_10_10_0_0_24"


def test_lag_addition_precedes_member_override(loaded):
    sw1 = _device(loaded, "AUS01-SW1")
    names = [list(i.values())[0] for i in sw1["interfaces"]]
    assert names.index("Po1") < names.index("Ethernet1")
    eth1 = [i for i in sw1["interfaces"] if i.get("!update:name") == "Ethernet1"][0]
    assert eth1["lag"] == "!ref:if_dal01_sw1_po1"
    assert eth1["untagged_vlan"] == "!ref:vlan_10"


def test_assignment_endpoints_get_ref_entries(loaded):
    sw2 = _device(loaded, "AUS01-SW2")
    iface_names = [list(i.values())[0] for i in sw2["interfaces"]]
    assert "Ethernet2" in iface_names  # untouched template port, needed by IP assignment
    assignments = loaded["designs/0001_site.yaml.j2"]["ip_address_to_interfaces"]
    flagged = [a for a in assignments if a.get("is_primary")]
    assert len(flagged) == 1 and flagged[0]["is_source"] is True


def test_vlans_use_per_site_group_and_no_lookup(loaded):
    doc = loaded["designs/0001_site.yaml.j2"]
    assert doc["vlan_groups"][0]["!create_or_update:name"] == "AUS01-vlans"
    for vlan in doc["vlans"]:
        assert "!create:name" in vlan
        assert vlan["vlan_group"] == "!ref:vlan_group_site"


def test_primary_ip_second_pass(loaded):
    devices = loaded["designs/0002_primary_ips.yaml.j2"]["devices"]
    by_name = {d["!update:name"]: d for d in devices}
    assert by_name["AUS01-SW1"]["primary_ip4"] == "!ref:ip_10_10_0_11_24"


def test_cabling_document(loaded):
    devices = loaded["designs/0003_cabling.yaml.j2"]["devices"]
    sw1 = [d for d in devices if d["!update:name"] == "AUS01-SW1"][0]
    iface_cable = [i for i in sw1["interfaces"] if i["!update:name"] == "Ethernet3"][0]
    assert iface_cable["!connect_cable"]["to"] == {
        "device__name": "AUS01-SW2",
        "name": "Ethernet3",
    }
    power = [i for i in sw1.get("power_ports", []) if i["!update:name"] == "PSU1"][0]
    assert power["!connect_cable"]["to"]["power_panel__name"] == "AUS01-PP-1"


def test_action_tags_are_quoted_strings(documents, loaded):
    # Keys beginning with '!' must survive dump -> Jinja -> safe_load as plain
    # quoted strings (Design Builder treats them as ordinary dict keys). The
    # `loaded` fixture parsing at all proves no key became a YAML tag; check
    # the raw text quotes them too.
    for _name, text in documents:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("!"):
                raise AssertionError(f"unquoted action tag in YAML: {line!r}")
    assert all(isinstance(doc, dict) for doc in loaded.values())


def test_deterministic_output(spec, param_map):
    first = render_design_documents(spec, param_map)
    second = render_design_documents(spec, param_map)
    assert first == second


def test_primary_ip_on_non_site_coded_device_refuses(spec_dict, param_map):
    from design_template_factory.spec import SiteSpec

    spec_dict["devices"][0]["name"] = "PLAINSW1"
    for entry in spec_dict["ip_assignments"] + spec_dict["primary_ips"]:
        if entry["device"] == "DAL01-SW1":
            entry["device"] = "PLAINSW1"
    for cable in spec_dict["cables"]:
        for end in ("a", "b"):
            if cable[end][0] == "DAL01-SW1":
                cable[end][0] = "PLAINSW1"
    with pytest.raises(RenderError, match="site-coded"):
        render_design_documents(SiteSpec.from_dict(spec_dict), param_map)


def test_propose_generates_reviewable_map(spec):
    proposal = propose(spec)
    assert proposal.source_site_code == "DAL01"
    assert [s["source"] for s in proposal.supernets] == ["10.10.0.0/24", "10.10.10.0/24"]
    assert proposal.name_patterns == [{"pattern": "(?i)DAL01", "replace": "{{ site_code }}"}]
    assert any("R2" in note for note in proposal.notes)


def test_every_created_object_is_stamped(loaded):
    """Plan §3.3/§4: every object this design creates carries the stamp.

    Applies to entries with a create-action tag (creations); v1 documented
    exceptions: cables and ip_address_to_interfaces through-rows.
    """
    doc = loaded["designs/0001_site.yaml.j2"]

    def assert_stamped(entry, where):
        creates = [k for k in entry if k.startswith("!create")]
        if creates:
            assert "provisioned_from" in (entry.get("custom_fields") or {}), (
                f"unstamped created object in {where}: {entry}"
            )
        for value in entry.values():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        assert_stamped(item, where)

    for family, entries in doc.items():
        if family == "ip_address_to_interfaces":
            continue
        for entry in entries:
            assert_stamped(entry, family)


def test_ref_handles_are_injective(spec_dict, param_map):
    """Distinct names that slug identically must get distinct !ref handles."""
    from design_template_factory.spec import SiteSpec

    spec_dict["devices"][0]["_components"]["interfaces"]["additions"].extend(
        [{"name": "Ethernet9/1"}, {"name": "Ethernet9.1"}]
    )
    documents = render_design_documents(SiteSpec.from_dict(spec_dict), param_map)
    loaded = _render_and_load(documents)
    refs = []

    def collect(node):
        if isinstance(node, dict):
            if "!ref" in node:
                refs.append(node["!ref"])
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(loaded["designs/0001_site.yaml.j2"])
    assert len(refs) == len(set(refs)), "duplicate !ref handles emitted"


def test_unicode_plus_jinja_literal_survives(spec_dict, param_map):
    """The quoting contract: unicode/control chars + Jinja delimiters in one
    literal must render back byte-exact without executing (the escape/YAML
    interaction that bricked packages in review finding #2)."""
    from design_template_factory.spec import SiteSpec

    hostile = "Café\trésumé {{ pwn() }} — end"
    spec_dict["locations"][0]["description"] = hostile
    documents = render_design_documents(SiteSpec.from_dict(spec_dict), param_map)
    loaded = _render_and_load(documents)  # StrictUndefined: execution would raise
    assert loaded["designs/0001_site.yaml.j2"]["locations"][0]["description"] == hostile


def test_hostile_custom_field_value_is_inert(loaded):
    sw1 = _device(loaded, "AUS01-SW1")
    assert sw1["custom_fields"]["site_owner"] == "{{ malicious }} team"


def test_vrfs_in_spec_are_refused(spec_dict, param_map):
    from design_template_factory.spec import SiteSpec

    spec_dict["vrfs"] = [{"name": "CORP", "rd": "65000:1"}]
    with pytest.raises(RenderError, match="VRF"):
        render_design_documents(SiteSpec.from_dict(spec_dict), param_map)


def test_rack_groups_topologically_sorted(spec_dict, param_map):
    """A child group sorting before its parent must still emit parent-first."""
    from design_template_factory.spec import SiteSpec

    spec_dict["rack_groups"] = [
        {"name": "AAA-child", "location": "DAL01/Floor 1", "parent": "DAL01-MDF", "custom_fields": {}},
        {"name": "DAL01-MDF", "location": "DAL01/Floor 1", "parent": None, "custom_fields": {}},
    ]
    documents = render_design_documents(SiteSpec.from_dict(spec_dict), param_map)
    loaded = _render_and_load(documents)
    groups = loaded["designs/0001_site.yaml.j2"]["rack_groups"]
    names = [next(v for k, v in g.items() if k.startswith("!create")) for g in groups]
    assert names.index("AUS01-MDF") < names.index("AAA-child")

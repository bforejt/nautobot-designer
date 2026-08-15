"""Resolver + diffing: the pure core of deploy and verify."""

import copy

import pytest

from design_template_factory.params import ParamMap, ParamMapError

from design_template_factory.materializer.diffing import diff_deployment, normalize
from design_template_factory.materializer.resolver import ResolveError, Seeds, resolve

SEEDS = Seeds(
    site_code="AUS01",
    site_name="Austin Branch",
    supernets={"supernet_1": "10.20.0.0/16"},
)


@pytest.fixture()
def plan(spec, param_map):
    return resolve(spec, param_map, SEEDS)


class TestResolver:
    def test_root_location_renamed(self, plan):
        root = plan["locations"][0]
        assert root["name"] == "Austin Branch"
        assert root["path"] == "Austin Branch"
        assert root["parent"] is None

    def test_child_location_path_rerooted(self, plan):
        child = plan["locations"][1]
        assert child["path"] == "Austin Branch/Floor 1"
        assert child["parent"] == "Austin Branch"

    def test_names_resolved(self, plan):
        assert [d["name"] for d in plan["devices"]] == ["AUS01-SW1", "AUS01-SW2"]
        assert plan["racks"][0]["name"] == "AUS01-R1"
        assert plan["racks"][1]["name"] == "R2"  # non-matching name passes through
        assert plan["power_panels"][0]["name"] == "AUS01-PP-1"

    def test_device_locations_use_new_paths(self, plan):
        assert plan["devices"][0]["location"] == "Austin Branch/Floor 1"
        assert plan["devices"][0]["rack"] == "AUS01-R1"

    def test_ipam_rebased(self, plan):
        assert [p["prefix"] for p in plan["prefixes"]] == ["10.20.0.0/24", "10.20.10.0/24"]
        assert [ip["address"] for ip in plan["ip_addresses"]] == [
            "10.20.0.11/24",
            "10.20.0.12/24",
        ]
        assert plan["primary_ips"][0] == {
            "device": "AUS01-SW1",
            "ip": "10.20.0.11/24",
            "family": 4,
        }

    def test_vlan_group_named_for_site(self, plan):
        assert plan["vlan_groups"] == [{"name": "AUS01-vlans"}]

    def test_cables_resolved(self, plan):
        assert plan["cables"][0]["a"] == ["AUS01-SW1", "interfaces", "Ethernet3"]
        assert plan["cables"][1]["b"] == ["AUS01-PP-1", "power_feeds", "FEED-A"]

    def test_components_carried(self, plan):
        sw1 = plan["devices"][0]
        assert sw1["_components"]["interfaces"]["overrides"][0]["untagged_vlan"] == 10
        sw2 = plan["devices"][1]
        assert sw2["_components"]["interfaces"]["removals"] == ["Ethernet4"]

    def test_missing_seed_rejected(self, spec, param_map):
        with pytest.raises(ResolveError, match="missing supernet seeds"):
            resolve(spec, param_map, Seeds("AUS01", "Austin", supernets={}))

    def test_undersized_seed_rejected(self, spec, param_map):
        with pytest.raises(ResolveError, match="smaller"):
            resolve(
                spec,
                param_map,
                Seeds("AUS01", "Austin", supernets={"supernet_1": "10.20.0.0/20"}),
            )

    def test_deterministic(self, spec, param_map):
        assert resolve(spec, param_map, SEEDS) == resolve(spec, param_map, SEEDS)


def _simulate_capture(plan):
    """What the walker would emit after a faithful deployment."""
    captured = copy.deepcopy(plan)
    captured["vlan_groups"] = []  # walker does not model group membership
    for family, entries in captured.items():
        for entry in entries:
            if isinstance(entry, dict) and "custom_fields" in entry:
                entry["custom_fields"] = {
                    **entry["custom_fields"],
                    "provisioned_from": "branch-small@1/AUS01",
                }
    return captured


class TestDiffing:
    def test_faithful_deployment_diffs_clean(self, plan):
        assert diff_deployment(plan, _simulate_capture(plan)) == []

    def test_cable_orientation_is_normalized(self, plan):
        captured = _simulate_capture(plan)
        cable = captured["cables"][0]
        cable["a"], cable["b"] = cable["b"], cable["a"]
        assert diff_deployment(plan, captured) == []

    def test_wrong_rack_detected(self, plan):
        captured = _simulate_capture(plan)
        captured["devices"][0]["rack"] = "R2"
        diffs = diff_deployment(plan, captured)
        assert diffs and any("rack" in d for d in diffs)

    def test_missing_device_detected(self, plan):
        captured = _simulate_capture(plan)
        captured["devices"].pop()
        diffs = diff_deployment(plan, captured)
        assert any("length" in d for d in diffs)

    def test_component_removal_divergence_detected(self, plan):
        captured = _simulate_capture(plan)
        captured["devices"][1]["_components"] = {}
        diffs = diff_deployment(plan, captured)
        assert any("_components" in d for d in diffs)

    def test_extra_ip_flag_detected(self, plan):
        captured = _simulate_capture(plan)
        captured["ip_assignments"][1]["is_standby"] = True
        diffs = diff_deployment(plan, captured)
        assert any("is_standby" in d for d in diffs)

    def test_normalize_drops_stamp_and_nones(self, plan):
        captured = _simulate_capture(plan)
        norm = normalize(captured)
        as_text = str(norm)
        assert "provisioned_from" not in as_text
        assert "None" not in as_text


class TestReviewRegressions:
    def test_vrfs_refused(self, spec_dict, param_map):
        from design_template_factory.spec import SiteSpec

        spec_dict["vrfs"] = [{"name": "CORP", "rd": "65000:1"}]
        with pytest.raises(ResolveError, match="vrfs"):
            resolve(SiteSpec.from_dict(spec_dict), param_map, SEEDS)

    def test_power_feed_cable_endpoint_renamed_with_feed(self, spec_dict, param_map):
        from design_template_factory.spec import SiteSpec

        spec_dict["power_feeds"][0]["name"] = "DAL01-FEED-A"
        spec_dict["cables"][1]["b"][2] = "DAL01-FEED-A"
        plan = resolve(SiteSpec.from_dict(spec_dict), param_map, SEEDS)
        assert plan["power_feeds"][0]["name"] == "AUS01-FEED-A"
        assert plan["cables"][1]["b"] == ["AUS01-PP-1", "power_feeds", "AUS01-FEED-A"]

    def test_resolved_name_collision_detected(self, spec_dict):
        from design_template_factory.spec import SiteSpec

        pmap = ParamMap.from_dict(
            {
                "schema": "1.0",
                "template": {"id": "branch-small", "source_site_code": "DAL01"},
                "supernets": [{"seed": "supernet_1", "source": "10.10.0.0/16"}],
                "rules": {"name_patterns": [{"pattern": "(?i)DAL01", "replace": "{{ site_code }}"}]},
            }
        )
        spec_dict["devices"][1]["name"] = "dal01-SW1"  # collapses onto DAL01-SW1
        for entry in spec_dict["ip_assignments"] + spec_dict["primary_ips"]:
            if entry["device"] == "DAL01-SW2":
                entry["device"] = "dal01-SW1"
        for cable in spec_dict["cables"]:
            for end in ("a", "b"):
                if cable[end][0] == "DAL01-SW2":
                    cable[end][0] = "dal01-SW1"
        with pytest.raises(ResolveError, match="same\\s+identity|same identity"):
            resolve(SiteSpec.from_dict(spec_dict), pmap, SEEDS)

    def test_bad_supernet_cidr_rejected_at_map_load(self):
        with pytest.raises(ParamMapError, match="host bits"):
            ParamMap.from_dict(
                {
                    "schema": "1.0",
                    "template": {"id": "x", "source_site_code": "DAL01"},
                    "supernets": [{"seed": "supernet_1", "source": "10.10.1.5/16"}],
                    "rules": {},
                }
            )

    def test_rack_group_out_of_spec_parent_detected(self, spec_dict):
        from design_template_factory.spec import SiteSpec

        spec_dict["rack_groups"][0]["parent"] = "CAMPUS-GROUP"
        problems = SiteSpec.from_dict(spec_dict).validate()
        assert any("CAMPUS-GROUP" in p for p in problems)

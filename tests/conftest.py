"""Shared fixtures: an in-memory spec mirroring the lab fixture site."""

import pytest

from design_template_factory.params import ParamMap
from design_template_factory.spec import SiteSpec


def _empty_reference_families():
    from design_template_factory import constants

    return {family: [] for family in constants.REFERENCE_FAMILIES}


@pytest.fixture()
def spec_dict():
    references = _empty_reference_families()
    references.update(
        {
            "location_types": [{"name": "Site"}, {"name": "Floor"}],
            "statuses": [{"name": "Active"}, {"name": "Connected"}],
            "roles": [{"name": "Access Switch"}],
            "manufacturers": [{"name": "FixtureNet"}],
            "device_types": [
                {
                    "manufacturer": "FixtureNet",
                    "model": "FX-48",
                    "template_fingerprint": "f" * 64,
                }
            ],
            "namespaces": [{"name": "Global"}],
        }
    )
    return {
        "_meta": {
            "spec_schema": "1.0",
            "template_version": "0",
            "scope_tier": "v1-core+cables+power",
            "source": {
                "nautobot": "3.1.7",
                "site_code": "DAL01",
                "location": "DAL01",
                "location_type": "Site",
                "parent_location_type": "Region",
            },
            "target": {"design_builder": ">=3.1,<4"},
            "lint": [
                {
                    "severity": "warning",
                    "category": "non-site-coded-name",
                    "message": "rack R2 does not embed the site code",
                    "obj": "R2",
                }
            ],
        },
        "_references": references,
        "_parameters": {},
        "locations": [
            {
                "name": "DAL01",
                "path": "DAL01",
                "location_type": "Site",
                "parent": None,
                "status": "Active",
                "description": "Golden site {{ malicious }} fixture",
                "custom_fields": {},
            },
            {
                "name": "Floor 1",
                "path": "DAL01/Floor 1",
                "location_type": "Floor",
                "parent": "DAL01",
                "status": "Active",
                "description": None,
                "custom_fields": {},
            },
        ],
        "rack_groups": [
            {"name": "DAL01-MDF", "location": "DAL01/Floor 1", "parent": None, "custom_fields": {}}
        ],
        "racks": [
            {
                "name": "DAL01-R1",
                "location": "DAL01/Floor 1",
                "rack_group": "DAL01-MDF",
                "status": "Active",
                "u_height": 42,
                "custom_fields": {},
            },
            {
                "name": "R2",
                "location": "DAL01/Floor 1",
                "rack_group": "DAL01-MDF",
                "status": "Active",
                "u_height": 42,
                "custom_fields": {},
            },
        ],
        "power_panels": [
            {"name": "DAL01-PP-1", "location": "DAL01/Floor 1", "rack_group": None, "custom_fields": {}}
        ],
        "power_feeds": [
            {
                "name": "FEED-A",
                "power_panel": "DAL01-PP-1",
                "status": "Active",
                "voltage": 120,
                "custom_fields": {},
            }
        ],
        "vlan_groups": [],
        "vlans": [
            {"vid": 10, "name": "USERS", "status": "Active", "custom_fields": {}},
            {"vid": 20, "name": "VOICE", "status": "Active", "custom_fields": {}},
        ],
        "vrfs": [],
        "prefixes": [
            {
                "prefix": "10.10.0.0/24",
                "namespace": "Global",
                "status": "Active",
                "custom_fields": {},
            },
            {
                "prefix": "10.10.10.0/24",
                "namespace": "Global",
                "status": "Active",
                "custom_fields": {},
            },
        ],
        "devices": [
            {
                "name": "DAL01-SW1",
                "device_type": {"manufacturer": "FixtureNet", "model": "FX-48"},
                "role": "Access Switch",
                "status": "Active",
                "platform": None,
                "tenant": None,
                "location": "DAL01/Floor 1",
                "rack": "DAL01-R1",
                "position": 1,
                "face": "front",
                "custom_fields": {"site_owner": "{{ malicious }} team"},
                "_components": {
                    "interfaces": {
                        "additions": [{"name": "Po1", "type": "lag"}],
                        "overrides": [
                            {
                                "name": "Ethernet1",
                                "description": "uplink {{ malicious }} literal",
                                "untagged_vlan": 10,
                                "tagged_vlans": [20],
                                "lag": "Po1",
                            }
                        ],
                    }
                },
            },
            {
                "name": "DAL01-SW2",
                "device_type": {"manufacturer": "FixtureNet", "model": "FX-48"},
                "role": "Access Switch",
                "status": "Active",
                "platform": None,
                "tenant": None,
                "location": "DAL01/Floor 1",
                "rack": "R2",
                "position": 2,
                "face": "front",
                "custom_fields": {},
                "_components": {
                    "interfaces": {"removals": ["Ethernet4"]},
                },
            },
        ],
        "ip_addresses": [
            {
                "address": "10.10.0.11/24",
                "namespace": "Global",
                "status": "Active",
                "custom_fields": {},
            },
            {
                "address": "10.10.0.12/24",
                "namespace": "Global",
                "status": "Active",
                "custom_fields": {},
            },
        ],
        "ip_assignments": [
            {
                "ip": "10.10.0.11/24",
                "device": "DAL01-SW1",
                "interface": "Ethernet2",
                "is_primary": True,
                "is_source": True,
            },
            {"ip": "10.10.0.12/24", "device": "DAL01-SW2", "interface": "Ethernet2"},
        ],
        "primary_ips": [
            {"device": "DAL01-SW1", "ip": "10.10.0.11/24", "family": 4},
            {"device": "DAL01-SW2", "ip": "10.10.0.12/24", "family": 4},
        ],
        "cables": [
            {
                "a": ["DAL01-SW1", "interfaces", "Ethernet3"],
                "b": ["DAL01-SW2", "interfaces", "Ethernet3"],
                "status": "Connected",
            },
            {
                "a": ["DAL01-SW1", "power_ports", "PSU1"],
                "b": ["DAL01-PP-1", "power_feeds", "FEED-A"],
                "status": "Connected",
            },
        ],
    }


@pytest.fixture()
def spec(spec_dict):
    return SiteSpec.from_dict(spec_dict)


@pytest.fixture()
def param_map():
    return ParamMap.from_dict(
        {
            "schema": "1.0",
            "template": {"id": "branch-small", "source_site_code": "DAL01"},
            "supernets": [{"seed": "supernet_1", "source": "10.10.0.0/16"}],
            "rules": {
                "name_patterns": [{"pattern": "DAL01", "replace": "{{ site_code }}"}],
            },
        }
    )

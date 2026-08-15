"""Deterministic golden-site fixture for the Phase A spike and round-trip CI.

Builds the small-but-complete site from the plan (§7 step 1) in a lab
Nautobot (composer stack), deliberately seeding every hostile case the spike
must exercise:

- a rack whose name contains no site code   (identifier-downgrade path)
- a group-less VLAN                          (per-site VLANGroup handling)
- an interface IP with non-default role flags (through-model capture)
- a device with a template-born interface REMOVED (no-delete-verb lint)
- an added (non-template) interface + LAG    (component additions/ordering)
- a description containing '{{ malicious }}' (injection escaping)
- a hostile custom-field value               (escape via custom_fields)
- a RelationshipAssociation to an external location (scope-skip lint)
- an IP in a non-default namespace           (namespace pinning)
- cables incl. a power feed cable            (endpoint-pair serialization)

Run inside the Nautobot environment:

    nautobot-server nbshell --plain < fixtures/build_fixture_site.py
    # or: nautobot-server shell_plus, then exec(open('fixtures/...').read())

Idempotent-ish: uses get_or_create keyed on names; safe to re-run on a lab DB.
NOT for production instances.
"""

SITE_CODE = "DAL01"

from nautobot.dcim.models import (  # noqa: E402
    Cable,
    Device,
    DeviceType,
    Interface,
    Location,
    LocationType,
    Manufacturer,
    PowerFeed,
    PowerPanel,
    PowerPort,
    Rack,
    RackGroup,
)
from nautobot.dcim.models.device_component_templates import (  # noqa: E402
    InterfaceTemplate,
    PowerPortTemplate,
)
from nautobot.extras.models import Role, Status  # noqa: E402
from nautobot.ipam.models import (  # noqa: E402
    VLAN,
    IPAddress,
    IPAddressToInterface,
    Namespace,
    Prefix,
)

active = Status.objects.get(name="Active")
connected = Status.objects.get(name="Connected")
namespace = Namespace.objects.get(name="Global")


# --- Location tree -----------------------------------------------------------
region_type, _ = LocationType.objects.get_or_create(name="Region")
site_type, _ = LocationType.objects.get_or_create(
    name="Site", defaults={"parent": region_type}
)
if site_type.parent_id is None:  # pre-existing lab type without the chain
    site_type.parent = region_type
    site_type.validated_save()
floor_type, _ = LocationType.objects.get_or_create(
    name="Floor", defaults={"parent": site_type, "nestable": False}
)
for location_type in (site_type, floor_type):
    for model in (Rack, Device, Prefix, VLAN, PowerPanel):
        from django.contrib.contenttypes.models import ContentType

        location_type.content_types.add(ContentType.objects.get_for_model(model))

region, _ = Location.objects.get_or_create(
    name="South Central", location_type=region_type, defaults={"status": active}
)
site, _ = Location.objects.get_or_create(
    name=SITE_CODE,
    location_type=site_type,
    defaults={
        "status": active,
        "parent": region,
        # hostile case: injection attempt in captured literal
        "description": "Golden site {{ malicious }} fixture",
    },
)
if site.parent_id is None:
    site.parent = region
    site.validated_save()
floor, _ = Location.objects.get_or_create(
    name="Floor 1", location_type=floor_type, parent=site, defaults={"status": active}
)

# --- Racks -------------------------------------------------------------------
rack_group, _ = RackGroup.objects.get_or_create(name=f"{SITE_CODE}-MDF", location=floor)
rack1, _ = Rack.objects.get_or_create(
    name=f"{SITE_CODE}-R1", location=floor, defaults={"status": active, "rack_group": rack_group}
)
# hostile case: rack name without the site code
rack2, _ = Rack.objects.get_or_create(
    name="R2", location=floor, defaults={"status": active, "rack_group": rack_group}
)

# --- Device types ------------------------------------------------------------
manufacturer, _ = Manufacturer.objects.get_or_create(name="FixtureNet")
switch_type, _ = DeviceType.objects.get_or_create(
    manufacturer=manufacturer, model="FX-48", defaults={"u_height": 1}
)
for index in range(1, 5):
    InterfaceTemplate.objects.get_or_create(
        device_type=switch_type, name=f"Ethernet{index}", defaults={"type": "1000base-t"}
    )
PowerPortTemplate.objects.get_or_create(device_type=switch_type, name="PSU1")

# --- VLANs -------------------------------------------------------------------
vlan_users, _ = VLAN.objects.get_or_create(
    vid=10, name="USERS", vlan_group=None, defaults={"status": active}
)  # hostile case: group-less VLAN
vlan_voice, _ = VLAN.objects.get_or_create(
    vid=20, name="VOICE", vlan_group=None, defaults={"status": active}
)
for vlan in (vlan_users, vlan_voice):
    vlan.locations.add(site)

# --- Prefixes / IPs ----------------------------------------------------------
prefix_mgmt, _ = Prefix.objects.get_or_create(
    network="10.10.0.0", prefix_length=24, namespace=namespace, defaults={"status": active}
)
prefix_users, _ = Prefix.objects.get_or_create(
    network="10.10.10.0", prefix_length=24, namespace=namespace, defaults={"status": active}
)
for prefix in (prefix_mgmt, prefix_users):
    prefix.locations.add(site)

# --- Devices -----------------------------------------------------------------
role, _ = Role.objects.get_or_create(name="Access Switch")
from django.contrib.contenttypes.models import ContentType  # noqa: E402

role.content_types.add(ContentType.objects.get_for_model(Device))

devices = {}
for index, rack in ((1, rack1), (2, rack2)):
    device, _ = Device.objects.get_or_create(
        name=f"{SITE_CODE}-SW{index}",
        defaults={
            "device_type": switch_type,
            "role": role,
            "status": active,
            "location": floor,
            "rack": rack,
            "position": index,
            "face": "front",
        },
    )
    devices[index] = device

sw1, sw2 = devices[1], devices[2]

# hostile case: remove a template-born interface from SW2
Interface.objects.filter(device=sw2, name="Ethernet4").delete()

# hostile case: added (non-template) LAG + member override
lag, _ = Interface.objects.get_or_create(
    device=sw1, name="Po1", defaults={"type": "lag", "status": active}
)
eth1 = Interface.objects.get(device=sw1, name="Ethernet1")
eth1.description = "uplink {{ malicious }} literal"
eth1.mode = "tagged"  # trunk: untagged + tagged VLANs (hostile m2m case)
eth1.untagged_vlan = vlan_users
eth1.lag = lag
eth1.validated_save()
eth1.tagged_vlans.set([vlan_voice])

# hostile case: custom-field value carrying Jinja delimiters
from nautobot.extras.models import CustomField  # noqa: E402

cf, _ = CustomField.objects.get_or_create(
    key="site_owner", defaults={"label": "Site Owner", "type": "text"}
)
cf.content_types.add(ContentType.objects.get_for_model(Device))
sw1._custom_field_data["site_owner"] = "{{ malicious }} team"
sw1.validated_save()

# hostile case: relationship association to a location OUTSIDE the site
from nautobot.extras.models import Relationship, RelationshipAssociation  # noqa: E402

external_site, _ = Location.objects.get_or_create(
    name="EXT-DR-SITE",
    location_type=site_type,
    defaults={"status": active, "parent": region},
)
relationship, _ = Relationship.objects.get_or_create(
    key="backup_site",
    defaults={
        "label": "Backup Site",
        "type": "one-to-one",
        "source_type": ContentType.objects.get_for_model(Location),
        "destination_type": ContentType.objects.get_for_model(Location),
    },
)
RelationshipAssociation.objects.get_or_create(
    relationship=relationship,
    source_type=ContentType.objects.get_for_model(Location),
    source_id=site.pk,
    destination_type=ContentType.objects.get_for_model(Location),
    destination_id=external_site.pk,
)

# hostile case: IP in a non-default namespace, on a site-assigned prefix
oob_ns, _ = Namespace.objects.get_or_create(name="OOB")
oob_prefix, _ = Prefix.objects.get_or_create(
    network="192.168.0.0", prefix_length=24, namespace=oob_ns, defaults={"status": active}
)
oob_prefix.locations.add(site)
oob_ip, _ = IPAddress.objects.get_or_create(
    address="192.168.0.11/24", namespace=oob_ns, defaults={"status": active}
)
eth4_sw1 = Interface.objects.get(device=sw1, name="Ethernet4")
IPAddressToInterface.objects.get_or_create(ip_address=oob_ip, interface=eth4_sw1)

# --- IP addressing (incl. role-flag hostile case) ---------------------------
ip1, _ = IPAddress.objects.get_or_create(
    address="10.10.0.11/24", namespace=namespace, defaults={"status": active}
)
ip2, _ = IPAddress.objects.get_or_create(
    address="10.10.0.12/24", namespace=namespace, defaults={"status": active}
)
mgmt1 = Interface.objects.get(device=sw1, name="Ethernet2")
mgmt2 = Interface.objects.get(device=sw2, name="Ethernet2")
assignment1, _ = IPAddressToInterface.objects.get_or_create(
    ip_address=ip1, interface=mgmt1, defaults={"is_primary": True, "is_source": True}
)
assignment2, _ = IPAddressToInterface.objects.get_or_create(
    ip_address=ip2, interface=mgmt2
)
sw1.primary_ip4 = ip1
sw1.validated_save()
sw2.primary_ip4 = ip2
sw2.validated_save()

# --- Power -------------------------------------------------------------------
panel, _ = PowerPanel.objects.get_or_create(
    name=f"{SITE_CODE}-PP-1", location=floor
)  # site-coded: panels are cable targets
feed, _ = PowerFeed.objects.get_or_create(
    name="FEED-A", power_panel=panel, defaults={"status": active}
)

# --- Cables ------------------------------------------------------------------
eth3_sw1 = Interface.objects.get(device=sw1, name="Ethernet3")
eth3_sw2 = Interface.objects.get(device=sw2, name="Ethernet3")
if eth3_sw1.cable is None:
    Cable.objects.create(
        termination_a=eth3_sw1, termination_b=eth3_sw2, status=connected
    )
psu1 = PowerPort.objects.get(device=sw1, name="PSU1")
if psu1.cable is None:
    Cable.objects.create(termination_a=psu1, termination_b=feed, status=connected)

print(f"Fixture site {SITE_CODE} ready: run the Capture Site Template job against it.")

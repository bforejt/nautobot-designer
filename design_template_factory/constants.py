"""Shared constants: spec schema version, creation order, scope, strip lists."""

SPEC_SCHEMA_VERSION = "1.0"
PARAM_MAP_SCHEMA_VERSION = "1.0"

# Design Builder version range the renderer targets. Designs are regenerated,
# not migrated, when this moves.
DESIGN_BUILDER_TARGET = ">=3.1,<4"

SCOPE_TIER_V1 = "v1-core+cables+power"

# Spec keys holding created objects, in strict creation order. This is the
# verified 9-tier dependency order collapsed to the v1 scope; the renderer
# emits top-level design keys in exactly this order because Design Builder
# executes a design document top-down.
CREATION_ORDER = (
    "locations",
    "rack_groups",
    "racks",
    "power_panels",
    "power_feeds",
    "vlan_groups",   # the per-site VLANGroup is a CREATED object (globally-unique name)
    "vlans",
    "vrfs",
    "prefixes",
    "devices",
    "ip_addresses",
    "ip_assignments",
    "primary_ips",   # second-pass !update on devices (deferred primary IP pattern)
    "cables",        # rendered as !connect_cable second-pass entries
)

# Spec sections that are not created objects.
META_KEY = "_meta"
REFERENCES_KEY = "_references"
PARAMETERS_KEY = "_parameters"

# Tier-0 shared object families a design may only reference, never create or
# mutate. The capture job records these (natural keys only) so the generated
# design's pre-flight validators can verify-or-fail before the engine runs.
REFERENCE_FAMILIES = (
    "location_types",
    "statuses",
    "roles",
    "tags",
    "manufacturers",
    "device_types",   # each carries a component-template fingerprint (drift guard)
    "platforms",
    "tenants",
    "namespaces",
    "custom_fields",
)

# Attributes stripped at capture: instance identity and computed state that
# must never replay into a clone.
STRIP_FIELDS = frozenset(
    {
        "id",
        "created",
        "last_updated",
        "serial",
        "asset_tag",
        "mac_address",
        "facility_id",
        "url",
        "display",
        "natural_slug",
    }
)

# Device component families, keyed by the Django related_name on Device and
# carrying the Design Builder design key used for nested entries.
COMPONENT_FAMILIES = {
    "interfaces": "interfaces",
    "console_ports": "console_ports",
    "console_server_ports": "console_server_ports",
    "power_ports": "power_ports",
    "power_outlets": "power_outlets",
    "front_ports": "front_ports",
    "rear_ports": "rear_ports",
    "device_bays": "device_bays",
    "module_bays": "module_bays",  # captured for lint only in v1 (modules are v2 scope)
}

# IPAddressToInterface role flags: real data, easy to miss (verified trap).
IP_ASSIGNMENT_FLAGS = (
    "is_source",
    "is_destination",
    "is_default",
    "is_preferred",
    "is_primary",
    "is_secondary",
    "is_standby",
)

# Cable termination content-types supported in v1 scope, mapped to the
# component family used to locate the endpoint on a device. Power feeds
# terminate on PowerPanel-owned PowerFeed objects rather than devices and are
# handled specially.
CABLE_ENDPOINT_TYPES = {
    "dcim.interface": "interfaces",
    "dcim.consoleport": "console_ports",
    "dcim.consoleserverport": "console_server_ports",
    "dcim.powerport": "power_ports",
    "dcim.poweroutlet": "power_outlets",
    "dcim.frontport": "front_ports",
    "dcim.rearport": "rear_ports",
    "dcim.powerfeed": "power_feeds",
}

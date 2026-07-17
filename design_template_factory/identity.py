"""Per-model action-tag and identifier policy.

This encodes open engineering question #1 from the plan (§3.3): Design
Builder identifier tags perform a SINGLE-FIELD lookup, but most site-scoped
models only have compound natural keys. A non-unique lookup can silently
match — and mutate — another site's object, which is the worst failure mode
in the whole design.

v1 policy (the "scheme 3 floor", pending spike results):

- ``create_or_update`` ONLY where the looked-up field is globally unique by
  construction:
    * devices / racks / rack_groups: the rewrite stage guarantees names embed
      the site code, and the renderer refuses to emit otherwise;
    * vlan_groups: VLANGroup.name is globally unique in Nautobot;
    * prefixes / ip_addresses: values are re-prefixed into supernets the
      deploy form declares, unique within the shared namespace by
      construction.
- plain ``create`` everywhere a unique single field cannot be guaranteed
  (e.g. VLANs: (group, vid) is compound). ``!create`` performs no lookup, so
  it can never mutate a foreign object — a duplicate fails the transaction
  instead, which is the safe direction.

SPIKE-TODO: if the spike proves compound/dict-form identifier lookups or
nesting-scoped create_or_update, upgrade the CREATE entries here.
"""

from __future__ import annotations

ACTION_CREATE = "create"
ACTION_CREATE_OR_UPDATE = "create_or_update"
ACTION_UPDATE = "update"

# spec key -> (action, identifier field or None)
IDENTIFIER_POLICY: dict[str, tuple[str, str | None]] = {
    "locations": (ACTION_CREATE, "name"),              # (parent, name) compound key
    "rack_groups": (ACTION_CREATE_OR_UPDATE, "name"),  # site-coded name enforced
    "racks": (ACTION_CREATE_OR_UPDATE, "name"),        # site-coded name enforced
    "power_panels": (ACTION_CREATE, None),
    "power_feeds": (ACTION_CREATE, None),
    "vlan_groups": (ACTION_CREATE_OR_UPDATE, "name"),  # globally unique in Nautobot
    "vlans": (ACTION_CREATE, None),                    # compound key (group, vid)
    "vrfs": (ACTION_CREATE, None),
    "prefixes": (ACTION_CREATE_OR_UPDATE, "prefix"),   # unique post-re-prefixing
    "devices": (ACTION_CREATE_OR_UPDATE, "name"),      # site-coded name enforced
    "ip_addresses": (ACTION_CREATE_OR_UPDATE, "address"),
}

# Families whose names MUST embed the site code before the renderer will
# emit a create_or_update for them (the global-uniqueness guarantee).
SITE_CODED_FAMILIES = ("rack_groups", "racks", "devices")


def action_tag(spec_key: str) -> tuple[str, str | None]:
    """Return (action, identifier_field) for a created-object spec key."""
    try:
        return IDENTIFIER_POLICY[spec_key]
    except KeyError as err:
        raise KeyError(f"no identifier policy for spec key {spec_key!r}") from err


def design_key(action: str, identifier_field: str | None) -> str:
    """Compose the quoted Design Builder action-tag key."""
    if identifier_field:
        return f"!{action}:{identifier_field}"
    return f"!{action}"

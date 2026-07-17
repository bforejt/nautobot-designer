"""Device component diffing against DeviceType templates.

Device.save() auto-instantiates components from the DeviceType's templates
(verified core behavior), so capture must record the DELTA per device —
overrides to template-born components, additions, removals — never the full
component list, or replay double-creates.

Also computes the DeviceType template fingerprint used as the drift guard;
the algorithm here MUST stay in lockstep with the copy embedded in generated
design packages (design_template_factory/render/job.py::FINGERPRINT_SNIPPET —
which is literally this module's fingerprint code, embedded at codegen time).
"""

from __future__ import annotations

import hashlib

TEMPLATE_RELATED_NAMES = (
    "console_port_templates",
    "console_server_port_templates",
    "power_port_templates",
    "power_outlet_templates",
    "interface_templates",
    "rear_port_templates",
    "front_port_templates",
    "device_bay_templates",
    "module_bay_templates",
)

# component family -> (device related_name, device_type template related_name)
FAMILY_SOURCES = {
    "interfaces": ("interfaces", "interface_templates"),
    "console_ports": ("console_ports", "console_port_templates"),
    "console_server_ports": ("console_server_ports", "console_server_port_templates"),
    "power_ports": ("power_ports", "power_port_templates"),
    "power_outlets": ("power_outlets", "power_outlet_templates"),
    "front_ports": ("front_ports", "front_port_templates"),
    "rear_ports": ("rear_ports", "rear_port_templates"),
    "device_bays": ("device_bays", "device_bay_templates"),
}

# Fields compared against the template counterpart (template attr may not
# exist for every family; missing template attrs compare against the model
# default via _TEMPLATE_DEFAULTS).
_TEMPLATED_FIELDS = ("type", "label", "description", "mgmt_only")
_TEMPLATE_DEFAULTS = {"label": "", "description": "", "mgmt_only": False, "enabled": True}

# Live-only fields: any non-default value is an override by definition.
_INTERFACE_LIVE_FIELDS = ("enabled", "mtu", "mode")


def fingerprint_device_type(device_type) -> str:
    parts = []
    for related in TEMPLATE_RELATED_NAMES:
        manager = getattr(device_type, related, None)
        if manager is None:
            continue
        for template in manager.all():
            attrs = [
                related,
                template.name,
                str(getattr(template, "type", "")),
                str(getattr(template, "label", "") or ""),
                str(getattr(template, "description", "") or ""),
                str(getattr(template, "mgmt_only", "")),
                str(getattr(template, "positions", "")),
                str(getattr(template, "rear_port_position", "")),
                getattr(getattr(template, "rear_port_template", None), "name", ""),
            ]
            parts.append(":".join(attrs))
    return hashlib.sha256("|".join(sorted(parts)).encode("utf-8")).hexdigest()


def _template_value(template, field):
    if template is not None and hasattr(template, field):
        value = getattr(template, field)
        return "" if value is None else value
    return _TEMPLATE_DEFAULTS.get(field, "")


def _delta_dict(component, family: str, template) -> dict:
    """Capture only the fields that differ from the template (or defaults)."""
    data: dict = {"name": component.name}

    for field in _TEMPLATED_FIELDS:
        if not hasattr(component, field):
            continue
        live = getattr(component, field)
        live_cmp = "" if live is None else live
        template_cmp = _template_value(template, field)
        if str(live_cmp) != str(template_cmp):
            data[field] = live if isinstance(live, (bool, int)) else str(live)

    if family == "interfaces":
        for field in _INTERFACE_LIVE_FIELDS:
            live = getattr(component, field, None)
            default = _TEMPLATE_DEFAULTS.get(field)
            if live not in (None, "", default):
                data[field] = live if isinstance(live, (bool, int)) else str(live)
        if getattr(component, "untagged_vlan", None) is not None:
            data["untagged_vlan"] = component.untagged_vlan.vid
        tagged = (
            sorted(vlan.vid for vlan in component.tagged_vlans.all())
            if hasattr(component, "tagged_vlans")
            else []
        )
        if tagged:
            data["tagged_vlans"] = tagged
        if getattr(component, "lag", None) is not None:
            data["lag"] = component.lag.name

    if family == "front_ports" and getattr(component, "rear_port", None) is not None:
        template_rear = getattr(
            getattr(template, "rear_port_template", None), "name", None
        )
        if component.rear_port.name != template_rear:
            data["rear_port"] = component.rear_port.name
            if getattr(component, "rear_port_position", None) is not None:
                data["rear_port_position"] = component.rear_port_position

    return data


def diff_device_components(device) -> tuple[dict, list]:
    """Return ({family: {overrides, additions, removals}}, lint findings)."""
    from design_template_factory import lint

    result: dict = {}
    findings: list = []

    for family, (related, template_related) in FAMILY_SOURCES.items():
        manager = getattr(device, related, None)
        if manager is None:
            continue
        template_manager = getattr(device.device_type, template_related, None)
        templates = (
            {t.name: t for t in template_manager.all()}
            if template_manager is not None
            else {}
        )

        # Module-hosted components are v2 scope; device-owned only in v1.
        try:
            live_components = list(manager.filter(module__isnull=True))
        except Exception:  # family without a module FK
            live_components = list(manager.all())

        overrides, additions = [], []
        live_names = set()
        for component in live_components:
            live_names.add(component.name)
            if component.name in templates:
                delta = _delta_dict(component, family, templates[component.name])
                if len(delta) > 1:  # anything beyond 'name' is a real override
                    overrides.append(delta)
            else:
                additions.append(_delta_dict(component, family, None))

            for extra in ("vrf", "parent_interface", "bridge"):
                if getattr(component, extra, None) is not None:
                    findings.append(
                        lint.Finding(
                            severity="warning",
                            category="scope-skip",
                            obj=f"{device.name}/{component.name}",
                            message=f"interface {extra} is set but not captured in v1",
                        )
                    )

        removals = sorted(set(templates) - live_names)
        if removals:
            findings.append(
                lint.Finding(
                    severity="warning",
                    category="component-removal",
                    obj=f"{device.name}",
                    message=(
                        f"{family}: {', '.join(removals)} exist on the DeviceType "
                        "template but were removed from this device. Design Builder "
                        "has no delete action — clones WILL have these components. "
                        "(v1 limitation; excluded from round-trip diff.)"
                    ),
                )
            )

        if overrides or additions or removals:
            family_result: dict = {}
            if overrides:
                family_result["overrides"] = sorted(overrides, key=lambda c: c["name"])
            if additions:
                family_result["additions"] = sorted(additions, key=lambda c: c["name"])
            if removals:
                family_result["removals"] = removals
            result[family] = family_result

    module_bays = getattr(device, "module_bays", None)
    if module_bays is not None:
        try:
            has_modules = module_bays.filter(installed_module__isnull=False).exists()
        except Exception:  # field name differs across versions — stay noisy but honest
            has_modules = module_bays.exists()
        if has_modules:
            findings.append(
                lint.Finding(
                    severity="warning",
                    category="scope-skip",
                    obj=device.name,
                    message="device has installed modules — modules are v2 scope and "
                    "are NOT captured; clones will only have template-defined bays",
                )
            )
    return result, findings

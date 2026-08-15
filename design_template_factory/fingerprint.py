"""DeviceType component-template fingerprint — the drift guard.

Lives in the library so both the capture walker and deploy preflight share
one implementation (they MUST agree byte-for-byte).
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

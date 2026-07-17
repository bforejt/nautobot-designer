"""Parameter application helpers: name patterns and IP re-prefixing.

Re-prefixing follows the Phase 0 decision (shared namespace, offset-preserving
supernet map): every captured prefix/IP is expressed at render time as a
netutils ``network_offset`` Jinja expression against the deploy-time supernet
seed, so `new = seed_base + (old - source_base)` with prefix lengths kept.
Design Builder's Jinja environment includes the netutils filters (verified).
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable

from .escape import Placeholder, escape_literal


class RewriteError(ValueError):
    """A value could not be rewritten under the parameter map."""


# --------------------------------------------------------------------- names
def apply_name_patterns(name: str, patterns: Iterable[dict]) -> str | Placeholder:
    """Apply parameter-map name patterns; Placeholder when a rewrite occurred.

    The captured name is Jinja-escaped BEFORE patterns run: the replacement
    strings from the human-reviewed parameter map are the only trusted Jinja
    that may enter the resulting Placeholder. Without this, a hostile source
    name like 'DAL01-{{ malicious }}' would ride into the design unescaped
    (the #258 injection class).
    """
    escaped = escape_literal(name)
    rewritten = escaped
    for rule in patterns:
        rewritten = re.sub(rule["pattern"], rule["replace"], rewritten)
    if rewritten != escaped:
        return Placeholder(rewritten)
    return name


def is_site_coded(value: str | Placeholder) -> bool:
    """True when a rendered name embeds the site_code placeholder.

    This is the global-uniqueness guarantee required before the renderer may
    emit a ``create_or_update`` lookup for devices/racks (identity.py).
    """
    return isinstance(value, Placeholder) and "site_code" in value


# ------------------------------------------------------------------ networks
def find_root(network: str, roots: Iterable[str]) -> str | None:
    """Return the source supernet (from the map) containing ``network``."""
    net = ipaddress.ip_network(network, strict=False)
    best: ipaddress._BaseNetwork | None = None
    for candidate in roots:
        root = ipaddress.ip_network(candidate)
        if root.version == net.version and net.subnet_of(root):
            if best is None or root.prefixlen > best.prefixlen:
                best = root
    return str(best) if best else None


def network_offset_expr(network: str, source_root: str, seed_var: str) -> Placeholder:
    """Jinja expression re-basing a prefix onto the seed supernet.

    ``network_offset("10.0.0.0/8", "0.0.20.0/24")`` -> ``10.0.20.0/24``; the
    offset operand is the source value minus the source root, keeping the
    original prefix length.
    """
    net = ipaddress.ip_network(network, strict=False)
    root = ipaddress.ip_network(source_root)
    if not net.subnet_of(root):
        raise RewriteError(f"{network} is not inside source supernet {source_root}")
    delta = int(net.network_address) - int(root.network_address)
    offset_addr = ipaddress.ip_address(delta) if net.version == 4 else ipaddress.IPv6Address(delta)
    # `| string` coerces the IPNetworkVar (netaddr object) before netutils.
    return Placeholder(
        "{{ %s | string | network_offset('%s/%d') }}" % (seed_var, offset_addr, net.prefixlen)
    )


def address_offset_expr(address: str, source_root: str, seed_var: str) -> Placeholder:
    """Same as network_offset_expr but for a host address like 10.10.20.5/24."""
    iface = ipaddress.ip_interface(address)
    root = ipaddress.ip_network(source_root)
    if iface.version != root.version or int(iface.ip) < int(
        root.network_address
    ) or int(iface.ip) > int(root.broadcast_address):
        raise RewriteError(f"{address} is not inside source supernet {source_root}")
    delta = int(iface.ip) - int(root.network_address)
    offset_addr = ipaddress.ip_address(delta) if iface.version == 4 else ipaddress.IPv6Address(delta)
    return Placeholder(
        "{{ %s | string | network_offset('%s/%d') }}"
        % (seed_var, offset_addr, iface.network.prefixlen)
    )


def compute_roots(prefixes: Iterable[str]) -> list[str]:
    """Minimal covering set: captured prefixes not contained in another one.

    These become the template's supernet seeds (one deploy-time IPNetworkVar
    each) in the proposed parameter map.
    """
    nets = [ipaddress.ip_network(p, strict=False) for p in prefixes]
    roots: list[str] = []
    for net in nets:
        if not any(
            other != net and other.version == net.version and net.subnet_of(other)
            for other in nets
        ):
            roots.append(str(net))
    # Deterministic order: version, then address.
    return sorted(set(roots), key=lambda p: (ipaddress.ip_network(p).version, ipaddress.ip_network(p)))

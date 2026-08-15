"""Parameter application helpers: name resolution and IP re-prefixing.

Post-decision (docs/implementation-approach-decision.md): everything here is
pure Python evaluated at deploy time by the resolver — nothing is emitted as
Jinja for a downstream engine anymore. Re-prefixing keeps the Phase 0 policy:
shared namespace, offset-preserving supernet map, prefix lengths preserved
(`new = target_base + (old - source_base)`).
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable

SITE_CODE_TOKEN = "{{ site_code }}"
SITE_NAME_TOKEN = "{{ site_name }}"


class RewriteError(ValueError):
    """A value could not be rewritten under the parameter map."""


# --------------------------------------------------------------------- names
def resolve_name(name: str, patterns: Iterable[dict], site_code: str) -> str:
    """Apply parameter-map name patterns, substituting the site-code token.

    Parameter maps store replacements with the ``{{ site_code }}`` token for
    continuity with captured drafts; the resolver substitutes the real seed.
    """
    resolved = name
    for rule in patterns:
        resolved = re.sub(rule["pattern"], rule["replace"], resolved)
    return resolved.replace(SITE_CODE_TOKEN, site_code)


def resolve_tokens(value: str, site_code: str, site_name: str) -> str:
    return value.replace(SITE_CODE_TOKEN, site_code).replace(SITE_NAME_TOKEN, site_name)


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


def rebase_network(network: str, source_root: str, target_root: str) -> str:
    """Re-base a prefix onto the target supernet, preserving host offsets."""
    net = ipaddress.ip_network(network, strict=False)
    source = ipaddress.ip_network(source_root)
    target = ipaddress.ip_network(str(target_root))
    if not net.subnet_of(source):
        raise RewriteError(f"{network} is not inside source supernet {source_root}")
    if target.version != source.version:
        raise RewriteError(f"{target_root} is IPv{target.version}; expected IPv{source.version}")
    if target.prefixlen > source.prefixlen:
        raise RewriteError(
            f"target supernet {target_root} is smaller than source {source_root}"
        )
    delta = int(net.network_address) - int(source.network_address)
    base = int(target.network_address) + delta
    return f"{ipaddress.ip_address(base)}/{net.prefixlen}"


def rebase_address(address: str, source_root: str, target_root: str) -> str:
    """Re-base a host address like 10.10.20.5/24 onto the target supernet."""
    iface = ipaddress.ip_interface(address)
    source = ipaddress.ip_network(source_root)
    target = ipaddress.ip_network(str(target_root))
    if iface.version != source.version or not iface.network.subnet_of(source):
        if int(iface.ip) < int(source.network_address) or int(iface.ip) > int(
            source.broadcast_address
        ):
            raise RewriteError(f"{address} is not inside source supernet {source_root}")
    delta = int(iface.ip) - int(source.network_address)
    base = int(target.network_address) + delta
    return f"{ipaddress.ip_address(base)}/{iface.network.prefixlen}"


def compute_roots(prefixes: Iterable[str]) -> list[str]:
    """Minimal covering set: captured prefixes not contained in another one.

    These become the template's supernet seeds (one deploy-time input each)
    in the proposed parameter map.
    """
    nets = [ipaddress.ip_network(p, strict=False) for p in prefixes]
    roots: list[str] = []
    for net in nets:
        if not any(
            other != net and other.version == net.version and net.subnet_of(other)
            for other in nets
        ):
            roots.append(str(net))
    return sorted(set(roots), key=lambda p: (ipaddress.ip_network(p).version, ipaddress.ip_network(p)))

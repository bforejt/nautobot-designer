# Phase 0 — Gating Decisions

**Date:** 2026-07-13
**Answered by:** Brian Forejt
**Context:** These answer the gating questions in [options-analysis.md](options-analysis.md) §5.

| # | Question | Decision |
|---|---|---|
| 1 | Target platform | **Nautobot 3.x** (on it or upgrading soon) |
| 2 | Structural variation across sites | **A few size tiers** — one golden-site template per tier (S/M/L) |
| 3 | Decommission / tracked lifecycle | **Nice-to-have** — tag-based teardown is acceptable; not a hard requirement |
| 4 | Volume | **1–5 new sites per year** |
| 5 | Exporter scope | **Full scope**: core DCIM/IPAM + cables & power + modules/virtual chassis + wireless/controllers + BGP-app models |
| 6 | IP strategy for clones | **Re-prefix within a shared namespace** (user supplies target supernets; exporter records offsets) |
| 7 | NTC roadmap inquiry | Brian will ask through his own channels |
| 8 | Adoption root cause | **Validated with users** — YAML authoring difficulty was the real blocker |

## What these decisions confirm

- **Option B stands as the MVP**: with lifecycle a nice-to-have, nothing forces the Design Builder engine adapter. Being on 3.x keeps that Phase-2 door open (patched, feature-current app) if tracked lifecycle is promoted to a requirement later.
- **Template model:** one exported spec per size tier. No parameterized counts needed in the spec schema for v1.
- **Instantiate inputs (v1 seed contract):** template/tier, site name + code, parent location, target supernet(s) for re-prefixing, VLAN policy. Namespace is fixed (shared) rather than an input.

## The one tension to manage: full scope vs. low volume

Scope decision #5 is maximal (modules/VC, wireless/controllers, and BGP-app models are the three hardest identity/serialization cases) while volume (#4) is 1–5 sites/year — the leanest payback profile. Building full scope up front is the slow path to first value. Mitigation, to be validated in the spike:

- **Phase the exporter by scope tier**: v1 = core DCIM/IPAM + cables + power; v2 = modules/VC + wireless/controllers; v3 = BGP-app models.
- The exporter's **lint report explicitly lists every object it encountered but skipped** (no silent fidelity gaps) — so a v1 export of a golden site that uses modules is honest about what the clone won't include.
- The spike against a real golden site tells us how much of each tier's objects actually exist in practice, which may re-order (or shrink) v2/v3.

## Next step

Step 2 spike (per options-analysis §5): run the exporter concept against one real golden site on the 3.x instance — walk the graph, emit a draft spec, produce the lint/skip report, and count what doesn't serialize cleanly. Output: a concrete build plan with real effort numbers per scope tier.

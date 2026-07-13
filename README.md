# nautobot-designer

Tooling to generate a full set of inter-related Nautobot components (locations, racks, devices, interfaces, cables, IPAM) with minimal user input, using an existing fully-built location as the template.

## Status

**Step 1 — options analysis (in review).** See [docs/options-analysis.md](docs/options-analysis.md).

Supporting research (adversarially fact-checked against primary sources, July 2026) lives in [docs/research/](docs/research/).

## Background

NTC's [Design Builder](https://github.com/nautobot/nautobot-app-design-builder) app is the incumbent framework for declarative Nautobot designs, but hand-authoring its YAML is difficult enough that adoption has been zero. This project uses an existing golden site as the template instead, with the Design Builder framework as an optional execution backend rather than an authoring requirement.

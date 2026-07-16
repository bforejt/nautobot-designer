# nautobot-designer

Tooling to generate a full set of inter-related Nautobot components (locations, racks, devices, interfaces, cables, IPAM) with minimal user input, using an existing fully-built location as the template.

## Status

**Strategy locked (2026-07-15): capture-to-design.** We build the template-capture side only; Design Builder is the deployment engine. Pipeline and build plan: [docs/capture-to-design-plan.md](docs/capture-to-design-plan.md).

Prior steps: [options analysis](docs/options-analysis.md) · [Phase 0 decisions](docs/phase0-decisions.md).

Supporting research (adversarially fact-checked against primary sources, July 2026) lives in [docs/research/](docs/research/).

## Background

NTC's [Design Builder](https://github.com/nautobot/nautobot-app-design-builder) app is the incumbent framework for declarative Nautobot designs, but hand-authoring its YAML is difficult enough that adoption has been zero. This project uses an existing golden site as the template instead, with the Design Builder framework as an optional execution backend rather than an authoring requirement.

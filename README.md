# nautobot-design-template-factory

Capture an existing, fully-built Nautobot location and turn it into a parameterized Design Builder template — so new sites (locations, racks, devices, interfaces, cables, IPAM) can be deployed with minimal user input and nobody ever hand-authors design YAML.

## Status

**Strategy locked (2026-07-15): capture-to-design.** We build the template-capture side only; Design Builder is the deployment engine. Pipeline and build plan: [docs/capture-to-design-plan.md](docs/capture-to-design-plan.md).

Prior steps: [options analysis](docs/options-analysis.md) · [Phase 0 decisions](docs/phase0-decisions.md).

Supporting research (adversarially fact-checked against primary sources, July 2026) lives in [docs/research/](docs/research/).

## Background

NTC's [Design Builder](https://github.com/nautobot/nautobot-app-design-builder) app is the incumbent framework for declarative Nautobot designs, but hand-authoring its YAML is difficult enough that adoption has been zero. This project generates those designs from an existing golden site instead: a capture job walks the site into a versioned site-spec, a reviewed parameter map defines what varies per site, and a renderer emits a ready-to-run Design Builder design package. Design Builder remains the deployment engine.

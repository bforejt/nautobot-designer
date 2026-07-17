# nautobot-design-template-factory

Capture an existing, fully-built Nautobot location and turn it into a parameterized Design Builder template — so new sites (locations, racks, devices, interfaces, cables, IPAM) can be deployed with minimal user input and nobody ever hand-authors design YAML.

## Status

**Strategy locked (2026-07-15): capture-to-design.** We build the template-capture side only; Design Builder is the deployment engine. Pipeline and build plan: [docs/capture-to-design-plan.md](docs/capture-to-design-plan.md).

Prior steps: [options analysis](docs/options-analysis.md) · [Phase 0 decisions](docs/phase0-decisions.md).

Supporting research (adversarially fact-checked against primary sources, July 2026) lives in [docs/research/](docs/research/).

## Layout

- `design_template_factory/` — pure-Python library: spec model, parameter-map
  proposer, Design Builder package renderer, `dtf` CLI. No Nautobot imports.
- `jobs/` — the **Capture Site Template** Nautobot job (load this repo as a
  Git *jobs* data source; pip-install the library into the Nautobot image).
- `fixtures/build_fixture_site.py` — deterministic golden-site fixture for the
  Phase A spike / round-trip CI (composer lab).
- `tests/` — pytest suite for the pure pipeline (`pip install -e ".[dev]" && pytest`).

## Pipeline usage

```
1. Run the "Capture Site Template" job against the golden site
   → download site-spec-<code>.json + capture-lint-report.md, review, commit
2. dtf propose-params site-spec-dal01.json -o param-map.yaml
   → review/edit the parameter map (the template's intent), commit
3. dtf render site-spec-dal01.json param-map.yaml -o rendered/
   → commit rendered/jobs/** to the design git repository
4. In Nautobot: load the design repo as a jobs data source, create the
   `provisioned_from` custom field (see generated README), run the generated
   "Deploy site from template" job — dry-run first, scratch location first.
```

## Background

NTC's [Design Builder](https://github.com/nautobot/nautobot-app-design-builder) app is the incumbent framework for declarative Nautobot designs, but hand-authoring its YAML is difficult enough that adoption has been zero. This project generates those designs from an existing golden site instead: a capture job walks the site into a versioned site-spec, a reviewed parameter map defines what varies per site, and a renderer emits a ready-to-run Design Builder design package. Design Builder remains the deployment engine.

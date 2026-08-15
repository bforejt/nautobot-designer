# nautobot-designer

Turn a fully-built Nautobot location into a blessed, parameterized site
template — then stamp out new sites (locations, racks, devices, interfaces,
cables, power, IPAM) from it with a four-field form. Deployment is direct
ORM through one shared, heavily-tested materialization library; every
deployment is machine-verified against its template.

## Status

**Strategy (approved 2026-08-11): authored standard, direct-ORM deployment.**
Decision record: [docs/implementation-approach-decision.md](docs/implementation-approach-decision.md).
First lab-deployable pass of all four jobs is in place — see
[docs/lab-runbook.md](docs/lab-runbook.md).

Prior steps: [options analysis](docs/options-analysis.md) ·
[Phase 0 decisions](docs/phase0-decisions.md) ·
[capture-to-design plan (superseded)](docs/capture-to-design-plan.md) ·
adversarially verified research in [docs/research/](docs/research/).

## The four jobs

| Job | Role |
|---|---|
| **Capture Site Template** | Walk an existing site → draft spec + draft param map + lint report; a human curates the draft into a blessed template in git |
| **Deploy Standard Site** | Blessed template + seeds (site code, name, parent, supernets) → resolve (pure) → preflight → execute (atomic ORM, stamped). Dry-run default; the resolved plan is the review artifact |
| **Verify Deployed Site** | Re-capture the deployed site, deep-diff against `resolve(template, seeds)` — 0 differences = accepted |
| **Teardown Deployed Site** | Delete exactly what a deployment stamped (`provisioned_from`), reverse order, dry-run default |

## Layout

- `design_template_factory/` — pure library: spec model, param maps,
  resolver, diffing, `dtf` CLI (no Nautobot imports).
- `design_template_factory/materializer/` — the shared library: resolver
  (pure), preflight + executor + teardown (the only ORM-touching code; the
  trap ledger from [docs/research/site-data-model.md](docs/research/site-data-model.md) handled once).
- `jobs/` — the four thin job wrappers (load this repo as a Git *jobs* data
  source; pip-install the library into the Nautobot image).
- `templates/` — blessed templates (`<id>/spec.json` + `param-map.yaml`);
  `branch-small` ships as the lab example.
- `fixtures/build_fixture_site.py` — deterministic hostile-case golden site
  for the composer lab.
- `tests/` — pytest for the pure pipeline (`pip install -e ".[dev]" && pytest`).

## CLI

```
dtf validate templates/branch-small/spec.json
dtf propose-params site-spec-dal01.json -o param-map.yaml
dtf resolve templates/branch-small/spec.json templates/branch-small/param-map.yaml \
    --site-code AUS01 --site-name "Austin Branch" --supernet supernet_1=10.20.0.0/16
```

## Background

NTC's [Design Builder](https://github.com/nautobot/nautobot-app-design-builder)
was evaluated in depth (and a full spec→design renderer was built and then
retired — see the decision record): its differentiated value is lifecycle
machinery this use case rated nice-to-have, while its DSL constraints carried
real cost. The architecture keeps what mattered from that phase — the
validated spec as system of record, the capture walker as verifier, the
round-trip acceptance gate — and deploys through the ORM directly.

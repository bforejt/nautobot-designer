# Capture-to-Design: Locked Strategy and Pipeline Plan

**Status:** Strategy locked (Brian, 2026-07-15)
**Supersedes:** the Option-B-first recommendation in [options-analysis.md](options-analysis.md) §5 — see "What changed and why" below.
**Grounding:** every mechanism referenced here was verified in the Step-1 research ([docs/research/](research/)); Design Builder specifics cite its source/issues as of v3.1.1 (June 2026), targeting Nautobot 3.x per [Phase 0](phase0-decisions.md).

---

## 1. Strategy statement

**We build the template-capture side only. Design Builder is the deployment engine.**

Capture an existing golden site → produce a parameterized template → render it into a Design Builder-compatible design package → engineers deploy new sites by running the generated design job in Nautobot. We do not build our own materializer, instantiate job, or teardown machinery.

### What changed vs. the options analysis, and why it holds up

The analysis recommended Option B (own materializer) with Design Builder as an optional Phase-2 adapter, driven by risk in Design Builder's *lifecycle* features. This strategy inverts that: Design Builder becomes the Day-1 deployment engine (Option D, with C's dict path available as a fallback render mode). That is a legitimate trade because:

- **The scope cut is real, with one honest caveat.** The materializer, teardown machinery, and transaction/journal engineering disappear — Design Builder already implements sequential creation, `!ref` handles, cable connection, dry-run, transaction rollback, and (optionally) deployment lifecycle. The caveat: the options analysis priced the design renderer *higher* than the materializer it replaces. This plan's renderer is cheaper than the one priced there (it emits value-substitution Jinja only, never structural loops/conditionals — the S/M/L tier decision removed that need), but a firm renderer estimate is a required Phase A exit deliverable, not an assumption. Note also that v1's re-run safety story is **transaction rollback → clean re-run**, not idempotency: open bug #207 means accidentally double-running a design against rack-positioned devices errors out ("U10 is already occupied") rather than converging — harmless but must be documented for operators.
- **The risky Design Builder surfaces are avoidable in v1.** The verified-open bugs cluster on *re-run/import* flows: #184 (import mode + `connect_cable`) only matters if we adopt the original golden site into a deployment — we don't need to; #207 (rack-position re-runs) only bites updates to an existing deployment — v1 is create-once; #220 (`next_prefix`) is irrelevant because our generator emits explicit prefixes. Greenfield creation — our path — is Design Builder's solid core.
- **The safety valve survives.** The captured **site-spec remains the system of record** (§3). Design Builder consumes a *rendered projection* of it. If Design Builder is ever abandoned or blocks us, the Option-B materializer can be built later against the same specs; nothing about this strategy burns that bridge.

**Accepted risk (explicit):** deployment now depends on a low-traction third-party app (15★, NTC-maintained, active releases). Mitigations: spec-as-system-of-record above; Nautobot 3.x target (patched v3.1.x line, per Phase 0); a scale/compat spike before committing build effort (§7).

---

## 2. The pipeline at a glance

```
 STAGE 1: CAPTURE          STAGE 2: TEMPLATIZE        STAGE 3: RENDER              STAGE 4: DEPLOY (Design Builder — not ours)
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────────┐      ┌──────────────────────────────┐
│ "Capture Site    │      │ Parameter map:   │      │ Generator emits a    │      │ Engineer runs the generated  │
│ Template" Job    │ spec │ classify every   │ spec │ design package:      │ git  │ Design Job in Nautobot UI:   │
│ picks a Location,│─────▶│ value as seed /  │─────▶│ • NNNN_design.yaml.j2│─────▶│ picks tier, enters site code │
│ walks the graph, │  +   │ derived /        │  +   │ • context.yaml + cls │ repo │ + parent location + supernets│
│ diffs components │ lint │ verbatim / strip │ param│ • DesignJob subclass │ data │ → dry-run → commit.          │
│ vs DeviceType    │ rpt  │ (auto-proposed,  │ map  │   per template tier  │ src  │ Design Builder creates the   │
│ templates, emits │      │ human-reviewed)  │      │ • README (review aid)│      │ site; lifecycle optional.    │
│ site-spec JSON   │      │                  │      │                      │      │                              │
└──────────────────┘      └──────────────────┘      └──────────────────────┘      └──────────────┬───────────────┘
                                                                                                 │
 STAGE 5: VERIFY  ◀──────────────────────────────────────────────────────────────────────────────┘
 Re-run Capture against the newly created site → spec′; deep-diff spec (parameters resolved) vs spec′
 must be ∅. Same check is the CI round-trip invariant and the per-deployment acceptance test.
```

One golden site is captured **per size tier** (S/M/L, per Phase 0 decision #2) → one design package per tier.

---

## 3. The artifacts

### 3.1 Site-spec JSON (Stage 1 output — the system of record)

A schema-versioned document, deliberately shaped as an **annotated superset of a Design Builder design dict**: top-level keys are model verbose-plural names in dependency order (the verified 9-tier order), values are lists of object attribute maps — plus annotation blocks a design doesn't carry:

```jsonc
{
  "_meta": {
    "spec_schema": "1.0",
    "source": {"nautobot": "3.1.x", "location": "DAL01", "captured": "…"},
    "target": {"design_builder": ">=3.1,<4"},
    "scope_tier": "v1-core+cables+power",
    "skipped": [ /* lint: objects seen but out of scope — never silent */ ]
  },
  "_references": { /* Tier-0 shared objects, natural keys only: statuses, roles,
                      device_types (+ a component-template fingerprint each — see
                      "template drift" below), platforms, tenants, namespace,
                      custom_field defs — pre-checked at deploy, never created */ },
  "_parameters": { /* declared slots + derivation rules — filled in Stage 2 */ },
  "locations": [ …relative subtree, LocationType chain recorded… ],
  "rack_groups": …, "racks": …, "power_panels": …, "power_feeds": …,
  "vlan_groups": [ /* the per-site '{{ site_code }}-vlans' group — CREATED, not referenced */ ],
  "vlans": …, "vrfs": …, "prefixes": …,
  "devices": [ { …attrs…, "_components": {"overrides": […], "additions": […], "removals": […]} } ],
  "ip_addresses": …, "ip_assignments": [ /* incl. role flags: is_primary, is_secondary… */ ],
  "primary_ips": [ …second-pass list… ],
  "cables": [ {"a": ["<device>", "interface", "Ethernet1"], "b": […], "status": …, "type": …, "label": …} ]
}
```

Two schema notes forced by the target engine:

- **Component `removals` are captured but v1 cannot replay them** — Design Builder's action vocabulary (`!get/!create/!update/!create_or_update`) has no delete verb. v1 policy: lint-flag removals loudly at capture and at render (in the generated README), and exclude them from the round-trip diff. If they matter in practice, the fix is a small custom `AttributeExtension` (the extensions API supports commit/rollback hooks) — deferred until a real template needs it.
- **Template drift guard:** `_references` existence checks aren't enough for DeviceTypes — templates edited after capture change what `Device.save()` auto-creates, breaking `!update`-by-name entries and the round-trip diff. Each DeviceType reference carries a fingerprint (hash of its component templates); the generated design's validation compares it at deploy time, and a mismatch means re-capture.

Implementation notes (v1, post-review): skipped-object reporting lives in `_meta.lint` (no separate `skipped` list); the blessed parameter map lives beside the spec in git (`param-map.yaml`) with `_parameters` reserved; and locations are keyed by root-relative `path`, because location names are only unique per parent.

Why the spec exists even though the target is Design Builder: parameterization operates on structured data (not YAML text); the round-trip diff (Stage 5) compares specs; Jinja-injection escaping is applied at render, not entangled with capture; and it keeps every future render target (Option B materializer, docs, diagrams) open. Shaping it design-dict-like makes Stage 3 mostly a projection rather than a transformation (verified: designs are plain dicts with quoted-string action tags — F1).

### 3.2 Parameter map (Stage 2 output — the human "intent" file)

Small reviewed YAML in git. This is the renaming/renumbering policy every ecosystem's prior art says cannot be inferred (netbox#1994, Terraform) — so we don't infer it; we **propose** it mechanically and have a human confirm it (~15-minute review, not authoring):

- **Seeds** (deploy-time form inputs, fixed contract from Phase 0): `site_code`, `site_name`, `parent_location`, `supernet_map` (source supernet → target supernet, shared-namespace re-prefix). v1 fixes VLAN policy to "keep source VIDs" — a `vlan_policy` seed is deferred until a real template needs re-numbering.
- The review is mechanical-first, human-confirmed. Expect the *first* capture of a tier to take real engineering attention (pattern-detection misses, cross-site edge decisions); subsequent re-captures of the same tier should be a quick diff review.
- **Derived rules** (proposed by scanning the capture): device/rack name patterns (detected source-site-code substrings → `{{ site_code }}`), IP rewriting by supernet offset (`new_host = new_base + (old_host − old_base)`, prefix lengths preserved), VLAN group `{{ site_code }}-vlans` (required — VLANGroup names are globally unique), primary IPs follow the IP map automatically.
- **Verbatim:** device_type/role/platform/status/tenant references, rack elevations, interface configs, cable topology, local config context (optionally with site variables substituted).
- **Stripped (enforced, not optional):** serials, asset tags, MACs, facility IDs, UUIDs, timestamps, CablePaths, dynamic-group memberships.

### 3.3 Design package (Stage 3 output — what Design Builder consumes)

Standard Design Builder jobs-in-git layout (verified packaging model, demo-designs style), loaded into Nautobot as a Git repository data source:

```
jobs/
  __init__.py                      # register_jobs(BranchSiteMedium, …)
  branch_site_medium/
    __init__.py                    # generated DesignJob subclass
    context/context.yaml           # seed vars + derived-value templates
    designs/0001_site.yaml.j2      # the generated design
    README.md                      # generated: what this template contains, lint summary
```

Generated design mechanics — **verified mechanisms** (from Design Builder source/docs):

- Top-level model keys in dependency order (document order = execution order).
- Cables via the contrib `!connect_cable` extension with `!ref`-registered endpoints (refs are backward-only — the generator orders definitions before uses). The generated DesignJob registers the extension via `Meta.extensions`. Cable status/type/label attributes and power-port→power-feed terminations are on the spike checklist — not doc-verified.
- The `deferred: true` per-field flag for late FK assignment exists (its canonical example is `primary_ip`).
- Jinja placeholders appear **only** where the parameter map says; every other literal is emitted escaped/`{% raw %}`-wrapped (the #258 injection class — a generated design must never let a captured description containing `{{` execute).
- Deterministic output: stable ordering and stable identifiers across regenerations → meaningful git diffs between template versions.
- `design_mode`: v1 ships `classic` (ad-hoc creation — Design Builder's solid core). Flipping a tier to `deployment` is a per-template flag — but note it is **forward-only**: sites already deployed in classic mode cannot be retro-adopted into deployments while #184 blocks import of cabled sites. Lifecycle was a nice-to-have in Phase 0, so this is acceptable.
- **Every created object is stamped** with a `provisioned_from` custom field (or tag): template id + template version + run identifier. In classic mode this is the *only* record of what a deployment created — it is what makes audit and a future tag-scoped teardown job possible, and it was a non-negotiable in the options analysis. The generator emits it on every object automatically.
- Generated `DesignJob.Meta` carries operational settings: `soft_time_limit`/`time_limit` sized to the tier (Celery defaults of 300s/600s SIGKILL a full-site run), a dedicated queue recommendation, and singleton locking so two deployments can't race.

**Inferred compositions — architecturally sound per the data-model research, but not doc-exampled; spike gate #2 must prove them** (the plan's §6 risk table treats them accordingly):

- `primary_ip4/6` via the second-pass pattern: device → interfaces → IP assignment → deferred/`!update` primary-IP set, matching `Device.clean()`'s requirement.
- **`!update` entries for template-born components** carrying only captured overrides, `!create_or_update` for non-template additions — never re-creating what DeviceType templates auto-instantiate.
- `IPAddressToInterface` through-rows with non-default role flags (`is_primary`, `is_secondary`…) — the design syntax for through-model creation is unverified.
- Namespace pinning on generated Prefixes/IPs (parent-prefix auto-resolution is per-namespace; the renderer pins parents via explicit `!ref`s, which needs confirmation).
- The site-root location's parent lookup by pk dict form (`parent: {id: <ObjectVar pk>}`).
- `!connect_cable` payloads carrying cable `type`/`label`, and cross-family endpoints (console port → console server port) where the `to:` query has no termination-model discriminator.

**Open engineering question #1 — the per-model identifier scheme (the most load-bearing unverified mechanism):**

`!create_or_update:<field>` performs a single-field ORM lookup, and #264 guidance says the field must be unique — but most site-scoped models have only **compound** natural keys (Device: location+tenant+name; Rack: rack_group+name; VLAN: group+vid; Prefix: namespace+network+length; Interface: device+name). A naive `"!create_or_update:name": "R01"` on a rack could raise `MultipleObjectsReturned` — or worse, **silently match and mutate another site's rack**. Candidate schemes, to be settled in the spike, per model:

1. **Nesting-based scoping** where the syntax supports it (children auto-associate with parents — verified for interfaces-under-device and locations; unverified for racks-under-location);
2. **Compound/dict-form lookups** under action tags, if supported (dict-form multi-criteria lookups are verified for query *fields*, not for identifier tags);
3. **Fallback that always works:** globally-unique synthetic names — the parameter map already injects `{{ site_code }}` prefixes into device/rack names, and re-prefixed IPs/prefixes are unique in the shared namespace by construction; the generator can enforce site-code-scoped naming for every nameable object as a hard rule.

Until proven otherwise, the generator assumes scheme 3 as the floor.

### 3.4 What the deploying engineer experiences (Stage 4 — zero build for us)

Jobs → "Deploy Branch Site (Medium)" → form: site code, site name, parent location (ObjectVar), target supernet(s) (`deployment_name` appears only if the tier is flipped to deployment mode) → dry-run (rendered design attached to the JobResult for inspection) → run. Design Builder executes inside its transaction; failures roll back.

Generated context-class `validate_*` methods carry the pre-flight burden, because Design Builder has **no upfront reference-verification phase** — a missing shared object otherwise fails mid-transaction via an ORM lookup error, surfaced through known error-reporting bugs (#232, #286) that make failures opaque. The generator therefore emits validators that check, at form-submit time: every `_references` entry exists (with content-type gating intact), the chosen parent location's LocationType chain matches the template's requirement, DeviceType template fingerprints match capture, supernets are large enough for the template's prefix footprint, and target names/prefixes don't already collide.

---

## 4. Design rules carried over from the options analysis

- **Tier-0 shared objects are read-only**: a design never creates or mutates statuses, roles, device types, custom fields. The generator emits them as query references (`status__name`, `device_type__model`+`manufacturer__name`), never as top-level creatable keys — with existence enforced by the generated pre-flight validators (§3.4), since query-reference syntax cannot silently create. (The per-site VLANGroup is the deliberate exception: it is a *created* object, not a reference.)
- **Every object stamped with a deployment identifier** (`provisioned_from` custom field/tag: template + version + run) — audit and future teardown depend on it, especially in classic mode where no ChangeSet exists.
- **Operational guardrails before first production run**: generated per-job time limits + dedicated queue + singleton lock (§3.3); RBAC on the generated jobs; first deployments land in a scratch/quarantine location with dry-run-first as policy; a webhook/change-log storm policy agreed with downstream-system owners (a full site fires thousands of events).
- **Round-trip is the acceptance criterion** (CI, on a factory-built fixture site in the composer lab, and per real deployment).
- **Lint before bless**: capture refuses to emit a spec without a lint report (orphan cables, missing primary IPs, component/template drift, cross-site edges, skipped-by-scope objects); a human blesses the template knowing exactly what it omits.
- **Cross-site edges are flagged, never cloned**: cables to circuit terminations or out-of-subtree devices; RelationshipAssociations pointing outside; location-scoped ConfigContexts (reported: "these contexts will NOT auto-apply to new sites" — policy decision surfaced, not silently mutated).
- **Schema versioning**: spec carries source Nautobot version + target Design Builder range; the generator refuses mismatches; re-capture after platform upgrades is policy.
- **Scope phasing** (Phase 0 tension mitigation): v1 = core DCIM/IPAM + cables + power; v2 = modules/VC + wireless/controllers; v3 = BGP-app models. Lint lists everything a lower tier skipped.

---

## 5. What we explicitly do not build

- Instantiate/materializer job (Design Builder's engine does it)
- Teardown job (deferred, not impossible: the `provisioned_from` stamp makes a scripted tag-scoped teardown buildable later; deployment-mode decommission covers it if a tier is flipped)
- Import/adoption of the *original* golden site into a deployment (blocked by #184 for cabled sites anyway; revisit if NTC fixes it)
- SSoT adapters, drift remediation
- Per-template custom UI (generated per-tier DesignJob forms are the UX)

---

## 6. Strategy-specific risks

| Risk | Exposure | Mitigation |
|---|---|---|
| **Identifier lookups match objects in other sites** (single-field `!create_or_update` on compound-keyed models) — silent cross-site mutation is the worst failure mode in the whole design | High until spike settles the scheme | Open question #1 (§3.3): nesting / compound lookups / site-code-scoped names (floor); **spike gates #3 and #4** (two coexisting deployments; source-site re-capture unchanged) |
| Design Builder can't execute a generated full-site design at scale (#111 history: 5k objects → 18h pre-rewrite; current overhead unmeasured; nonlinear blow-up is the documented failure shape) | High — this is now the deployment engine | **Spike gate #1**: run a synthetic design at genuine tier scale (hundreds of devices, factory-stamped) under production-like time-limit config; numeric pass bar set against Celery limits |
| Generated-design patterns are inferred, not doc-exampled (deferred primary-IP, `!update` on template-born components, IP-assignment role flags, namespace pinning) | Medium | **Spike gate #2**: hand-write a small design exercising every pattern; run it on composer 3.x |
| Component removals unexpressible (no delete verb in Design Builder) | Medium — depends on how curated golden sites are | v1: lint-flag + exclude from round-trip; later: custom AttributeExtension if real templates need it |
| Opaque failure UX (#232 JobResult save on validation error, #286 swallowed errors) | Medium | Generated pre-flight validators catch reference/fit problems before the engine runs (§3.4) |
| Jinja injection via captured literals (#258 class) | Medium | Escape-everything rule in the renderer + a render-time scanner test (fixture site contains `{{ malicious }}` strings) |
| Accidental re-run of a classic design errors on racked devices (#207) — safe (transaction rollback) but confusing | Low | Singleton lock on generated jobs; behavior documented in each generated README; test #207 scenario before flipping any tier to `deployment` |
| Design Builder version coupling (generated designs target 3.1.x semantics) | Medium | Spec is version-stamped; regeneration is cheap; spec-as-SoR keeps Option B fallback |
| Template drift between capture and deploy | Unchanged from analysis | Specs + packages versioned in git; deployments record template version (job/version Meta) |

---

## 7. Build plan

**Phase A — Spike (1–2 weeks, composer lab on 3.x + Design Builder 3.1.x):**

*Entry condition:* Brian's NTC roadmap inquiry (Phase 0 #7) answered or timed out — building what the vendor is about to ship remains the worst outcome.

1. Stand up composer 3.x stack; build a small but complete golden site via a **deterministic factory script** (2 racks, 4 devices, cabled + powered, prefixes/VLANs/IPs, primary IPs), deliberately seeded with the hostile cases: a rack whose name contains no site code, a group-less VLAN, an interface IP with non-default role flags, IPs in a non-default namespace, a custom-field value and a RelationshipAssociation, a device with a template-born component *removed*, a cross-family (console) cable, separator-variant component names (`Ethernet1/1` vs `Ethernet1.1`), and a description containing `{{ malicious }}`. (Implemented: `fixtures/build_fixture_site.py` — console cable and separator-variant names still TODO there.)
2. **Hand-write** the design YAML for that site (what the generator will eventually emit): candidate identifier schemes per model (open question #1), `!connect_cable` with status/type/label and a power-feed termination, deferred primary IPs, `!update` on template-born interfaces, IP-assignment role flags, namespace pinning. Deploy it to a new location. This settles every inferred pattern before a line of generator code exists (gate #2).
3. **Deploy the same design a second time** as another "site" (different code/supernets): both must coexist, which is what exposes identifier-collision failures (gate #3).
4. **Re-capture the source golden site** and diff against its original spec: must be ∅ — proves deployments didn't silently mutate source objects via bad lookups (gate #4).
5. Scale: factory-stamp a synthetic design at genuine tier scale (hundreds of devices) and run it under production-like Celery time-limit config; set a numeric pass bar (e.g., full tier deploy inside the per-job limit with margin) — extrapolation from 4 devices is not acceptable evidence given #111's nonlinear history (gate #1).
6. Prototype the capture walk (read-only) against the golden site → draft spec v0; eyeball against the hand-written design.
7. Exit deliverables: all four gates green; the settled per-model identifier scheme; a firm renderer effort estimate; the go/no-go call. **Any gate failure re-opens the Option-B fallback before real money is spent.**

**Phase B — Capture Job v1:** graph walk (v1 scope), component diffing, natural-key serialization, lint report, spec schema v1, tests on factory fixture site.

**Phase C — Templatize:** parameter-map proposer (name-pattern detection, supernet mapping) + review workflow (map file in git).

**Phase D — Renderer:** spec + map → design package (design.yaml.j2, context, DesignJob class, README); escaping; determinism tests; golden-file tests (same spec → byte-identical package).

**Phase E — Round-trip CI:** factory fixture site → capture → render → deploy to scratch location → re-capture → diff = ∅; runs against composer in CI.

**Phase F — Real-site pilot:** capture one production golden site per tier, review lint + parameter maps, deploy the next real site from it. Success metrics per Phase 0: time-to-provision vs. today, deployment correctness (round-trip diff), engineer feedback.

Then scope tiers v2/v3 per §4.

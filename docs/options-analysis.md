# Site Templating for Nautobot — Options Analysis

**Status:** Draft for review (Step 1 deliverable)
**Date:** 2026-07-12
**Research basis:** Four independent deep-dives (Design Builder internals, prior art, alternative approaches, Nautobot data model), each adversarially fact-checked against primary sources (source code, GitHub issues/releases, official docs), plus a completeness critique. Full findings with sources: [docs/research/](research/). All decision-critical claims (§2) were independently verified and none were refuted; peripheral notes on set-aside options draw on the gap-check research pass ([gaps.md](research/gaps.md)) and are marked by their sources there.

---

## 1. Problem and goal

Deploying a new site in Nautobot requires creating a large graph of inter-related objects (locations, racks, power, devices, interfaces, cables, prefixes, VLANs, IPs). NTC's Design Builder app was adopted for this, but hand-authoring its YAML designs is difficult and unintuitive — adoption has been zero.

**Goal:** a method to generate a full set of inter-related Nautobot components with **minimal user input**, using an **existing, fully-built location as the template**. Design Builder is optional — use it only where it genuinely helps, never at the cost of the main goal. Follow-up phase (out of scope here): drive site automation from Nautobot as the SSoT.

Two candidate shapes were proposed at kickoff:

- **(a)** an export Job that snapshots an existing site to JSON, paired with an instantiate Job that rebinds names/IPs and creates the objects; or
- **(b)** a Job that reverse-engineers an existing site into Design Builder YAML, keeping the Design Builder framework.

A key research finding (§2, F1) is that these are **not mutually exclusive** — most of the implementation (my estimate: on the order of 80%) is shared, and the choice between them can be deferred behind a stable intermediate format.

---

## 2. Decision-shaping findings

Each finding below was verified against primary sources; citations are in the linked research appendices.

### F1. Design Builder's engine accepts plain dicts — JSON works today; humans never need to author YAML

Verified in source: design files are parsed with `yaml.safe_load` after Jinja rendering, and the "action tags" (`"!create_or_update:name"`, `"!ref:eth1"`) are ordinary quoted strings, not YAML-native tags. A pure JSON design file is accepted with **zero upstream changes**. Deeper: `nautobot_design_builder.design.Environment().implement_design(design_dict, commit=True)` is directly callable from any Job with a plain Python dict — no `DesignJob` subclass, no Jinja, no YAML file. The shipped `build_design` management command does exactly this.

**Implication:** "export→JSON→instantiate" and "generate a design" converge. A machine-built design dict can be executed by Design Builder's engine (gaining its ORM resolution, `!create_or_update` idempotency, and dry-run) or by our own materializer — the artifact format does not force the framework decision. One caveat: the verified direct-dict path runs in *ad-hoc* mode; driving deployment-mode lifecycle from a dict (supplying a ChangeSet, or wrapping a minimal DesignJob) is architecturally plausible but **unverified** — a named spike question, not an established fact. Feeding dicts directly also bypasses Jinja entirely, which eliminates a template-injection risk class (F6).

### F2. No prior art exists — and the most instructive prior art is negative

No issue, PR, branch, blog post, or community app anywhere proposes exporting existing Nautobot objects into a design; the exporter is greenfield regardless of option. NetBox core **rejected** "Ability to clone entire sites" ([netbox#1994](https://github.com/netbox-community/netbox/issues/1994), 2018); Jeremy Stretch's rationale: the blocker isn't serialization, it's the **renaming/renumbering policy** — "much more practical to script out your own unique logic for replicating a site using either the REST API or local ORM." Eight years later no site-clone plugin exists in either ecosystem.

Cross-ecosystem analogues repeat the pattern: Terraformer (the flagship "generate IaC from existing infra" tool) was archived in March 2026; Terraform's native config generation is still experimental and emits over-specific literal output; `kubectl get → apply` needs a dedicated scrubbing tool; Ansible never got a facts-to-playbook generator. Universal failure modes: over-specific output, IDs-vs-natural-keys breakage, inability to infer what should be parameterized, and per-schema maintenance cost that kills generic tools. **Narrow, purpose-built, schema-pinned tools survive; generic reverse-engineering tools die.**

### F3. The unavoidable hard core is identical for every template-driven option

Every option that uses the golden site as a template (B–E below; Option A hand-codes the shape instead) requires the same two components that don't exist off the shelf:

1. **A site-graph exporter**: walk one Location subtree; serialize by natural key; distinguish site-scoped objects (create) from shared/global objects (reference-only: statuses, roles, device types, platforms, tenants, custom fields…); diff each device's components against its DeviceType templates; serialize cables as endpoint pairs; detect cross-site edges (circuits, inter-site cables, location-scoped config contexts) and flag them.
2. **A parameterization contract**: the explicit policy for every exported field — user seed (site name/code, parent location, prefix seeds), derived (device/rack names via pattern, IPs via supernet offset mapping, VLAN IDs via map/offset), or stripped (serials, asset tags, MACs, UUIDs, facility IDs, timestamps).

The engine that materializes objects afterward (plain ORM / Design Builder / DiffSync) is the comparatively small, swappable part.

### F4. Nautobot data-model traps every option must handle

Verified against core source (2.4 LTM and 3.x branches):

- **Component auto-creation (biggest replay trap):** `Device.save()` auto-instantiates all components from DeviceType templates. Replay must create the device, then **update** template-born components by name, create only the delta, and record deletions — the exporter must therefore compute a component-vs-template diff (and tolerate template drift). The 3.x development line adds a suppression hook for auto-creation (verify against the deployed release); 2.4 does not.
- **Cables have no identity:** `Cable.natural_key = ["pk"]`; terminations are generic FKs. Cabling must be serialized as `(device, component-type, component-name) ↔ (…)` pairs — a topology pattern, not objects. One-cable-per-endpoint uniqueness forces check-before-create on re-runs. `CablePath` is computed — never export it.
- **IPAM ordering:** `IPAddress` requires a parent `Prefix` in the target Namespace (creation hard-fails without one) → prefixes before IPs; **Namespace is a required clone parameter** (two valid strategies: same namespace + supernet re-mapping, or fresh per-site namespace with verbatim addresses). Interface↔IP is m2m through `IPAddressToInterface` with role flags that naive exports miss.
- **Primary IP is a two-phase write:** `Device.clean()` requires `primary_ip4/6` to already be assigned to one of the device's own interfaces → device+interfaces+IP assignments first, then a second-pass update.
- **VLANs:** natural key falls back to `pk` when `vlan_group` is null; VLANGroup names are globally unique → cloned sites need a new group (e.g. `{site}-vlans`) to give VLANs stable identity.
- **Locations:** natural keys are variadic ancestor-name chains; `LocationType` is immutable per location and gates attachable object types — the target's LocationType chain must match the source's.
- **Device identity shifts on 3.x:** the 3.x line replaces Device uniqueness with a `DEVICE_UNIQUENESS` setting whose default natural key is `pk` (medium confidence; verify against the exact target release) — the exporter should define its own device identity fields rather than leaning on core natural keys.
- **ConfigContexts are global and rule-scoped:** location-scoped contexts will **silently not apply** to a cloned location unless the shared object is mutated — a policy decision the tool must surface.
- A deterministic 9-tier creation order covers the whole graph (shared refs → locations → racks/power → prefixes/VLANs/VRFs → devices+components → IPs+assignments → primary-IP fixup → cables → virtual-chassis/relationship fixups). All known cycles have standard two-phase resolutions.

### F5. Design Builder's real value-add is lifecycle — and its bugs sit exactly on our critical path

The differentiators over a plain Job are deployment mode: named Deployments, ChangeSet/ChangeRecord journaling of every touched object/attribute, tracked re-runs (drift auto-decommission), first-class dependency-aware **decommission**, brownfield **import mode** (adopt an existing site into a deployment), and opt-in data protection. These work for the demo-style happy path, but verified-open bugs land on full-site cloning specifically:

- [#184](https://github.com/nautobot/nautobot-app-design-builder/issues/184) import mode broken with `connect_cable` (open since Aug 2024) — blocks adopting an existing *cabled* site into a deployment;
- [#207](https://github.com/nautobot/nautobot-app-design-builder/issues/207) rack-positioned devices not idempotent on re-run;
- [#220](https://github.com/nautobot/nautobot-app-design-builder/issues/220) `next_prefix` not idempotent (relevant only if allocation tags are used — a spec-driven generator emits explicit prefixes);
- docs' own TODO: update semantics "not working as expected" unless every object uses identifier tags consistently, with stable list ordering.

None were fixed through v3.1.1 (June 2026). Scale is also on the record: a ~5,000-object design once took 18 hours under the app's original atomic mode (issue #111 — since improved by a per-object rewrite, but deployment-mode overhead at 1,000+ objects has no published measurements; a spike question). The app is actively maintained by NTC (regular releases) but has ~15 GitHub stars — our zero-adoption experience is the norm, not the exception. Its extensions API is small and stable if we ever want custom action tags.

### F6. Version fork and a security landmine

- Design Builder **3.x requires Nautobot ≥3.0** (Nautobot 3.0 shipped Nov 2025; 3.1 is current stable). On **Nautobot 2.4 LTM** you are pinned to Design Builder **v2.3.0, feature-frozen** (critical fixes only; branch has had zero commits since Dec 2025).
- **Verified exposure:** Design Builder [#258](https://github.com/nautobot/nautobot-app-design-builder/issues/258) is a Jinja2 code-injection vulnerability (user job inputs evaluated as templates with ORM access). The fix shipped in **v3.1.0 only** — the 2.4-LTM line is **unpatched**. Any DesignJob-based option on Nautobot 2.4 carries a known injection flaw today.
- Same vulnerability class applies to option (b) generators: a generated design that passes through Jinja can execute literal site data containing `{{`/`{%`. Mitigation: feed dicts to `implement_design()` (bypasses Jinja) or escape every literal.
- Nautobot 2.4 LTM bundles Django 4.2, **EOL April 2026**. New tooling should target the 3.x line, or the platform upgrade becomes an explicit project dependency.

### F7. Operational constraints (apply to every option)

- **Job time limits:** Celery defaults are 300s soft / 600s hard (SIGKILL). A full-site instantiate (10³–10⁴ objects) needs per-job `soft_time_limit`/`time_limit` overrides and likely a dedicated queue. Decide: one `@transaction.atomic` (clean all-or-nothing, but long lock holds and total loss on late failure) vs phased commits with a creation journal for resume/cleanup. Note Jobs are **not** atomic by default in Nautobot 2.x+.
- **Side-effect storm:** every created object fires change-log writes, webhooks, and Job Hooks — one site instantiation can blast thousands of events at ITSM/monitoring/sync consumers. Need an explicit policy (let fire / suppress / warn downstream owners).
- **Validation-rule collision:** data-validation rules (core in 3.0; app on 2.4) run on every save — a template exported before a rule was added can fail mid-run. Pre-flight validation of the rendered object set is required.
- **Concurrency:** two simultaneous instantiations race on unique constraints; core's singleton-job lock (2.4+) is the available mitigation.

### F8. The make-or-break question is structural variation, not tooling

An exported site is a **concrete instance, not a template**. If real sites vary structurally (2 vs 8 access switches, 1 vs 3 IDFs, optional OOB), a pure snapshot cannot express loops/conditionals — the choices are multiple golden templates (S/M/L variants), post-clone editing, or reintroducing templating logic (which is exactly the authoring burden we're escaping). Similarly unvalidated: the assumption that YAML difficulty (rather than process/training/need) caused zero adoption, and the expected rate of new sites per year that would amortize the build. **These need internal answers before committing to a build** (§6, Phase 0).

---

## 3. Options

### Option A — Hand-written "site factory" Job (no template export)

A Python Job with minimal inputs (site name, parent location, prefix seed, counts) that creates the site via ORM. This is literally the documented pattern in Nautobot core's Jobs guide (the `NewBranch` example). Rich input forms (ObjectVar/StringVar/IPNetworkVar with chained filtering), dry-run via transaction rollback, no new dependencies.

- **Strengths:** lowest build cost (1–2 wks); pure-Python flexibility handles structural variation naturally (loops/conditionals); everything is documented core primitives.
- **Weaknesses:** the "template" is code — every topology change is a code change by whoever owns the job; doesn't use the existing golden site at all; idempotency and decommission are DIY.
- **Verdict:** the right floor to measure everything against; converges toward Option B as soon as the site shape is externalized into a data file.

### Option B — Export Job → versioned **site-spec JSON** → Instantiate Job (own materializer) — *kickoff shape (a)*

Exporter walks the golden site into a schema-versioned JSON spec (natural keys, component diffs vs device-type templates, cables as endpoint pairs, declared parameter slots). Specs live in a git repository (versioned, loadable via Nautobot's git data source). The instantiate Job applies the parameter map (names, IP supernet offsets, VLAN map, namespace) and creates objects in the deterministic 9-tier order inside a transaction, tagging everything with a deployment identifier (custom field or tag — the ownership-scoping pattern the SSoT Bootstrap integration uses) for auditability and a scripted teardown job.

**Parameter collection (explicit decision, since Job forms are flat and static):** v1 standardizes a fixed seed contract across all templates — site name/code, parent location, prefix seed(s), VLAN policy, namespace — so **one generic instantiate Job form** (template dropdown + seeds) serves every spec. If per-template parameters emerge, the exporter generates a **thin plain-Job class per spec** — the same per-template-form UX that Design Builder's packaging model provides, without the framework.

- **Strengths:** no third-party dependency; full control of ordering and failure handling; the spec is the durable design artifact — reviewed via the exporter's lint report and git diffs between versions (at full-site scale, no artifact of any format is readable end-to-end); matches what every ecosystem converged on (F2); portable across Nautobot 2.4→3.x with a schema-version stamp.
- **Weaknesses:** lifecycle (tracked updates, decommission) is DIY — tag-based teardown is cruder than Design Builder's ChangeSets; ~2–4 wks for the exporter+materializer with cables/IPAM edge cases.
- **Verdict:** **recommended MVP backbone** (see §4).

### Option C — Same exporter/spec, materialized through Design Builder's engine (dict → `implement_design`) — *hybrid*

Identical exporter and spec as B; a thin adapter converts the spec into a design dict and feeds Design Builder's `Environment`. The prize is deployment mode — ChangeSet journaling, tracked updates, first-class decommission, import-mode adoption of the original golden site. Caveat (F1): the verified dict path runs in ad-hoc mode; whether deployment-mode lifecycle can be driven from a dict (supplying a ChangeSet, or wrapping a minimal DesignJob) is **unverified** and is the first thing a Phase-2 spike must prove. If it turns out to require full DesignJob packaging, C's cost moves toward D's.

- **Strengths:** reuses B's hard work; buys the lifecycle features that are genuinely valuable and expensive to rebuild; no YAML/Jinja anywhere (no injection surface, F6); keeps the Design Builder investment alive.
- **Weaknesses:** inherits the app's bug surface exactly where we live (#184 cables-in-import, #207 rack idempotency, #220); requires Nautobot 3.x for a patched, feature-current app; deployment-mode update semantics demand strict identifier discipline from the generator; couples the runtime to a 15-star app (mitigated: the spec, not the design dict, is the system of record — Design Builder becomes a disposable render target).
- **Verdict:** **the Phase-2 adapter** — adopt only if tracked lifecycle/decommission is confirmed as a hard requirement, after a round-trip spike validates the bugs don't bite our shape.

### Option D — Generate Design Builder YAML artifacts (designs in git, executed as DesignJobs) — *kickoff shape (b)*

Same exporter, but emitting Jinja-parameterized design YAML packaged as DesignJob classes per template.

- **Strengths:** per-template Job classes give each template its own clean input form (this is Design Builder's packaging model working *for* us); artifacts are human-inspectable in git.
- **Weaknesses:** a fully-materialized real site is tens of thousands of YAML entries — no more reviewable than the spec it was rendered from, so the "design as intent" benefit only materializes if the generator emits parameterized Jinja (much harder — it's the intent-inference problem no ecosystem solved, F2); reintroduces the Jinja injection surface (F6); everything in C's cons.
- **Verdict:** not as the primary path. D's genuine advantage — a generated Job class per template with its own clean input form — is matched in B by generating thin plain-Job classes per spec (see Option B), without the framework. Cheap to add later as a *render target* from the spec (for humans who want to read a design, or to hand-tune one exceptional site).

### Option E — SSoT app with a "golden site template" DiffSync source

Model the site in DiffSync; the SSoT contrib layer auto-implements create/update/delete, idempotent convergence, and a diff-preview UI. Prior art exists (the Bootstrap integration syncs YAML-from-git → Nautobot with ownership scoping) but covers **no devices, racks, interfaces, or cables** — the DCIM half is new work (~10–15 models; cables and interface adoption are the documented pain points; 3–5 wks).

- **Strengths:** idempotent re-run = free drift remediation back to template; healthy, actively-maintained dependency; dry-run diff UI; full-DCIM model coverage is proven achievable by first-party SSoT adapters (Device42, ServiceNow, IP Fabric).
- **Weaknesses:** highest modeling effort; decommission is semi-DIY (sync an empty source with deletes enabled, ownership-scoped); re-run-to-converge may be an anti-goal (stamped sites legitimately diverge post-deployment).
- **Verdict:** the upgrade path **if** continuous convergence-to-template becomes a requirement; not the MVP.

### Considered and set aside

- **Ansible collection (`networktocode.nautobot`, 103 modules incl. cables):** idempotent + free check-mode, but lives outside the Nautobot UI/RBAC/JobResult and has no decommission story. Viable only if intake is already Ansible-driven.
- **Terraform provider:** v0.0.1-alpha; plan/apply/destroy would fit perfectly, but maturity disqualifies it.
- **Nautobot core features:** `clone_fields` is single-object form pre-fill; Welcome Wizard imports device types only; export templates are single-content-type. Nothing in core through 3.1 clones an object graph — and no one has even filed a core feature request for it.
- **Device-onboarding v4 ("sync from network"):** competes only if sites are physically built before SoT entry; our flow is SoT-first.
- **`dumpdata`/`loaddata` fixtures:** verified broken for Tags/GenericForeignKeys (nautobot#3666) and raw saves skip validation/auto-creation — a trap, not a shortcut.
- **LLM-assisted design authoring:** could draft/refactor designs from a snapshot with human review; worth a later experiment, but not a substitute for a deterministic exporter.

---

## 4. Comparison

| Criterion | A: Factory Job | B: Spec + own materializer | C: Spec + DB engine | D: Generated design YAML | E: SSoT/DiffSync |
|---|---|---|---|---|---|
| Build effort | Low (1–2 wk) | Med (2–4 wk) | B + ~1 wk adapter | B + high (param. YAML gen) | Med-High (3–5 wk) |
| Uses golden site as template | No | Yes | Yes | Yes | Yes |
| Minimal-input UX | Good (hand-built form) | Good (generic form; per-spec generated Jobs if needed) | Same as B | Good (per-template forms, native) | Good |
| Structural variation | Best (code) | S/M/L specs or spec-level params | same as B | Jinja (hard to generate) | Source-adapter code |
| Idempotent re-run | DIY | DIY (check-before-create) | Good (with identifier discipline; open bugs) | same as C | **Free** |
| Dry-run | Txn rollback | Txn rollback | `commit=False` built in | built in | **Free, with diff UI** |
| Decommission / teardown | DIY | Tag-scoped teardown job | **First-class** (ChangeSets) | First-class | Semi (ownership-scoped delete sync) |
| Track which sites came from which template version | DIY | Spec version stamped on deployment tag | ChangeSet + Deployment records | same as C | SoR custom field |
| New dependencies | none | none | design-builder (15★) | design-builder | ssot + diffsync (healthy) |
| Nautobot 2.4 viability | Yes | Yes | Frozen app (**#258 unpatched** if DesignJob path used) | Frozen app + **unpatched #258** | Yes |
| Key risk | Template = code | Lifecycle DIY | Open bugs #184/#207/#220 | Injection surface + unreviewable output | Effort; convergence may be anti-goal |

---

## 5. Recommendation

**Build the exporter and a versioned site-spec JSON as the system of record (Option B), architected so the materializer is swappable — with Design Builder (Option C) as a deliberate Phase-2 decision, not a Day-1 dependency.**

Rationale:

1. The exporter + parameterization contract is the bulk of the work and is required by every template-driven option (F3) — building it first defers the framework decision until we have real information. (Phase 0 gates whether it gets built at all: if the answers land on low volume + high structural variation, Option A is the honest floor.)
2. Every ecosystem that faced this problem converged on purpose-built export/instantiate with an explicit renaming/renumbering policy (F2). Generic and framework-heavy approaches died.
3. Design Builder's unique value (lifecycle) is real but sits behind verified-open bugs on exactly our object types (F5), a version fork, and an unpatched injection on the 2.4 line (F6). Committing the MVP to it converts those from "their bugs" into "our blockers." With the spec as the stable artifact, adopting the engine later is a thin adapter — and abandoning it later costs nothing.
4. Adoption (the actual goal) depends on the instantiate UX: pick a template from a dropdown (specs discovered from a git data source), enter site name + parent location + prefix seed + a few knobs, dry-run to preview, commit. That UX is achievable on any engine — B standardizes the seed contract for a single generic form, and can generate thin per-spec Job classes if templates need their own parameters (matching Design Builder's per-template packaging without adopting it) — so choose the engine by risk, not by UX.

Architecture sketch:

```
┌─────────────┐   export    ┌──────────────────────┐   instantiate   ┌──────────────────┐
│ Golden site │ ──────────▶ │ site-spec JSON (git) │ ──────────────▶ │  Materializer     │
│ (Location   │  Job walks  │ • schema-versioned   │  Job: template  │  Phase 1: ORM,    │
│  subtree)   │  graph,     │ • natural keys       │  picker + seed  │  9-tier order,    │
└─────────────┘  diffs vs   │ • component diffs    │  inputs, param  │  atomic txn, tags │
                 templates, │ • cables as pairs    │  mapping,       │  Phase 2 (opt):   │
                 lints,     │ • declared params    │  pre-flight     │  DB implement_    │
                 flags      │ • cross-site flags   │  validation,    │  design() for     │
                 x-site     └──────────────────────┘  dry-run        │  lifecycle        │
                 edges                                               └──────────────────┘
```

Non-negotiable design rules regardless of option (from verified findings):

- **Round-trip test as the acceptance criterion:** export golden site → instantiate into a scratch location → re-export → deep-diff must equal ∅ modulo declared parameters. This is the CI invariant (test fixtures come from factories or the exporter itself — `dumpdata` is verified-broken for Tags/generic FKs; run against both platform lines if 2.4→3.x straddling is required).
- **Shared objects are read-only:** instantiate treats Tier-0 objects (statuses, roles, device types, custom fields, platforms, tenants…) as verify-or-fail references — a spec must never create or mutate them. Specs are accepted only from the reviewed git source (no ad-hoc file uploads in v1), and the instantiate/teardown Jobs sit behind RBAC (and, on Nautobot 3.x, approval workflows for teardown).
- **First runs land in a scratch/quarantine location** (or a staging instance), with dry-run-before-commit as standing policy.
- Spec carries a **schema version + source Nautobot version**; instantiate refuses mismatched specs (template rot across upgrades is a verified failure mode).
- Exporter includes a **lint pass** (orphan cables, missing primary IPs, template drift, cross-site edges) with a human-readable report before a site is blessed as a template.
- Tag/custom-field every created object with a deployment identifier from day one (cheap ownership + teardown + audit; upgrade path to ChangeSets stays open).
- Per-job time-limit overrides + singleton lock + a webhook-storm policy before the first production run (F7).
- Scope v1 to core DCIM/IPAM without modules/virtual-chassis/wireless/BGP-app models unless the golden site actually uses them — each adds the hardest identity cases.

### Phase 0 — gating questions to answer before building (cheap, ~1 week)

1. **Target platform:** Nautobot 2.4 LTM or 3.x, and when is the 3.x upgrade? (Shapes the Design Builder decision entirely; 2.4 + DesignJob = unpatched injection #258.)
2. **Structural variation:** across the sites we'd stamp, how much does topology actually vary? (Determines: single template vs S/M/L specs vs parameterized counts.) — the make-or-break premise check (F8).
3. **Is decommission/tracked-update a hard requirement** or is tag-based teardown enough? (The single biggest differentiator between staying on B vs adding C.)
4. **Volume:** expected new sites per year. (A few/year may not amortize anything beyond Option A.)
5. **Scope contract:** cables? power? modules/VC? wireless/controllers? BGP-app models? config-context policy for cloned locations? namespace strategy (re-prefix vs per-site namespace)? non-ORM artifacts (rack/device images, git-sourced config contexts) explicitly in or out?
6. **Ask NTC** (one hour, #nautobot Slack / GitHub issue): is design generation/export on the Design Builder roadmap? They already experimented with lower-friction input (an unmerged Excel-import branch). Building what the vendor ships next quarter is the worst outcome.
7. **Root-cause check on zero adoption:** confirm with the intended users that authoring difficulty (not process/mandate/need) was the blocker.
8. **Maintenance owner:** which team owns the exporter through Nautobot upgrades, and on what update cadence? (The per-schema maintenance burden is the Terraformer failure mode applied internally.)

### Proposed Step 2 (after Phase 0 answers)

1-week spike: run the exporter concept against one real golden site — walk the graph, emit a draft spec, count what doesn't serialize cleanly (cables, cross-site edges, component drift), and produce the lint report. If lifecycle is in play, a companion spike: drive Design Builder deployment mode from a dict (or minimal DesignJob) at representative scale — this validates the unverified integration path (F1), the open bugs (F5), and the #111 performance history against our actual site shape. The spike output turns this analysis into a concrete build plan with real effort numbers.

---

## 6. Top risks and mitigations

| Risk | Mitigation |
|---|---|
| Premise failure: sites vary too much for snapshot templates | Phase 0 Q2 before any build; S/M/L template variants; parameterized counts in spec v2 |
| Exporter fidelity gaps (silent, discovered by downstream automation) | Round-trip diff as CI invariant; explicit in/out scope contract; lint report |
| Template rot across Nautobot upgrades | Schema-version stamp; re-export-after-upgrade policy; refuse mismatched specs |
| Design Builder dependency risk (bugs, version fork, low traction) | Spec is system of record; DB is an optional, disposable render/execute target |
| Jinja injection via generated designs / unpatched #258 on 2.4 | Feed dicts to `implement_design()` (no Jinja); require Nautobot 3.x for any DesignJob use |
| Mid-run death (time limits) strands half-built site | Time-limit overrides, dedicated queue, atomic txn or creation journal + resume |
| Webhook/change-log storm on instantiate | Policy decision + downstream-owner heads-up before first production run |
| Golden-site drift between export and instantiate | Specs versioned in git; deployments record spec version; periodic re-export diff |
| Zero adoption repeats for organizational reasons | Phase 0 Q7; pilot with 1–2 real upcoming sites; success metrics (time-to-provision, % sites via tool) |
| Malicious/careless spec mutates shared objects | Tier-0 read-only verify-or-fail rule; specs only from reviewed git source; RBAC + approval workflow on teardown |
| Tool orphaned internally (bus factor) | Named owning team (Phase 0 Q8); narrow schema-pinned scope; upgrade-cadence checklist |

---

## 7. References

- Research appendices (full findings, sources, and verification transcripts):
  - [Design Builder capabilities](research/design-builder-capabilities.md)
  - [Reverse-engineering prior art](research/reverse-engineering-prior-art.md)
  - [Alternative approaches](research/alternative-approaches.md)
  - [Site data model & serialization](research/site-data-model.md)
  - [Completeness critique (gaps & risks)](research/gaps.md)
- Key primary sources: [nautobot-app-design-builder](https://github.com/nautobot/nautobot-app-design-builder) (code, issues #158/#184/#207/#220/#258, releases), [Design Builder docs](https://docs.nautobot.com/projects/design-builder/en/latest/), [Nautobot core source & release notes](https://docs.nautobot.com/projects/core/en/stable/release-notes/), [netbox#1994](https://github.com/netbox-community/netbox/issues/1994), [nautobot-app-ssot docs](https://docs.nautobot.com/projects/ssot/en/latest/), [nautobot#3666](https://github.com/nautobot/nautobot/issues/3666).

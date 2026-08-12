# Implementation Approach: Design Builder vs. Custom AI-Maintained Jobs

**Status:** **Approved (Brian, 2026-08-11).** Supersedes the engine choice in [capture-to-design-plan.md](capture-to-design-plan.md): the pipeline's verification stages survive; the Design Builder render/deploy stages do not. The winning architecture (§3) is the build plan of record.
**Date:** 2026-08-11
**Method:** Structured adversarial debate — three steelman advocates (custom jobs / Design Builder / spec-hybrid), a full cross-examination round with mandatory concessions, then three independent judges (5-year maintainability, operational safety & governance, delivery velocity) scoring eight criteria, instructed to spot-check every citation against the repo's verified research and live upstream source. Full record: [research/implementation-debate.md](research/implementation-debate.md).

---

## 1. The question and the thesis under test

Brian's thesis: given a **detailed authored standard** (core switch stacks/VirtualChassis, NFV servers with VMs as firewalls/jump hosts, OpenGear OOB with standard console cabling, IPAM standards, software versions, custom relationships) and **AI-assisted development** (code changed quickly via prompts), the simplest path is a custom Nautobot Job per deployment type — and frameworks like Design Builder or SSoT add no real value.

Three premises changed since the strategy was last locked: the standard is *authored, not captured* (killing the reverse-engineering pipeline's reason to exist); the model is much richer than core DCIM; and AI makes both Python *and* YAML cheap to author — dissolving the "YAML is too hard" complaint in both directions.

## 2. Verdict

**The thesis substantially wins on the engine question: all three judges removed Design Builder from the critical path.** It placed last on every scorecard. But the debate *refined* the thesis into a more precise shape than "a custom job per type" — even Option A's own advocate conceded that raw per-type jobs with unstructured AI edits are indefensible, and revised the position accordingly.

Judge score totals (8 criteria × 10 points):

| Lens | A: custom jobs | B: Design Builder | C: spec-hybrid | Verdict |
|---|---|---|---|---|
| Maintainability (5-yr) | 61 | 52 | 62 | A+C hybrid |
| Safety & governance | 59 | 52 | 61 | "A-executed, C-governed" |
| Delivery velocity | 58 | 49 | 57 | Revised A (spec deferred behind triggers) |

All three verdicts are the **same architecture** with one narrow disagreement (§4).

## 3. The winning architecture ("A-executed, C-governed")

1. **One shared direct-ORM materialization library** — owns the 9-tier creation order, two-phase primary-IP and VirtualChassis writes, component update-by-name, cable check-before-create, and the rich-model families (Cluster/VM/VMInterface, RelationshipAssociation, software versions, cross-family console cables). Written once, unit- and lab-tested. All of these have documented ORM resolutions in [research/site-data-model.md](research/site-data-model.md).
2. **Per-tier (S/M/L) Nautobot Jobs as thin data modules** over that library. Tier topology is **data, never inline ORM code** — CI enforces that tier modules contain no ORM calls. A standard change is a reviewed data diff against an unchanged, tested engine; a new deployment type is a new data module.
3. **The capture walker survives as the round-trip verifier** (its most valuable role all along): deploy → re-capture → parameter-resolved deep-diff = ∅, as a hard per-deployment acceptance gate and CI invariant, plus drift auditing later.
4. **Non-negotiable guardrails** (all core Nautobot primitives, engine-independent): `@transaction.atomic` with per-job time limits + dedicated queue + singleton lock; `provisioned_from` stamping on every object; DryRunVar dry-run-first policy; Nautobot 3.0 approval workflows on the jobs; **tag-scoped reverse-order teardown job built in the first sprint** (not deferred — it's days of work and closes A's biggest lifecycle gap).
5. **Design Builder: off the critical path.** No SSoT app either — unless convergence becomes a requirement (§6).

## 4. The one open sub-decision: authored spec now, or typed-Python data with promotion triggers

Two judges (maintainability, safety) say the tier data should be the existing **schema-versioned, machine-validated site-spec JSON** from day one; the velocity judge says typed Python data structures now, promoted to the spec if triggers fire (tiers > ~5, a second consumer, structural conditionals).

**Recommendation: spec now.** The tiebreaker is the debate's single strongest unrebutted argument — one Option A itself filed under "cannot answer": *the acceptance oracle must be authored independently of the deployment path.* A golden-master fixture blessed from the library-under-test's own output bakes any blessing-time bug into the oracle forever; an authored spec is a genuinely independent, machine-checkable definition of "deployed correctly." Since the ~650-LOC spec layer (schema refusal, referential validation, offset math, lint) already exists and is tested, and A-done-well externalizes topology as data anyway, the marginal cost is days — and even the velocity judge conceded this argument "should eventually pull the winning A implementation toward C's schema."

## 5. Why Design Builder lost — stated fairly

**Not on capability.** The debate corrected earlier assumptions in B's favor, verified in upstream source during the rounds: the engine's model map is built generically from `apps.get_models()`, so VMs, clusters, VirtualChassis, and software versions are in-vocabulary; custom Relationships are first-class (`CustomRelationshipField` creates RelationshipAssociation rows from a one-line design attribute). Design Builder can express more of the rich standard than we previously credited.

It lost on four grounds:

1. **Undifferentiated value at our requirements.** Its unique differentiator is deployment lifecycle (ChangeSets, journaled decommission, data protection) — which Phase 0 rated nice-to-have, twice, at 1–5 sites/year. The audit floor everyone actually relies on (`provisioned_from` stamp + ObjectChange log + JobResult) is engine-independent.
2. **Its "bounded artifact" safety claim is partially false — and that was decisive.** The best argument for B was that a design diff is reviewable *data* that "cannot express a deletion." Verified against the evidence base: design files are Jinja templates rendered with ORM-reachable context, and the #258 fix (PR #259) deliberately kept Jinja evaluation for context files/classes. Reviewers promised a bounded artifact would be reviewing one that can execute arbitrary mutations — a false sense of boundedness is itself a hazard. No advocate or judge dislodged this.
3. **The risk surface sits exactly on our object types**: single-field identifier lookups on compound-keyed models (High-rated cross-site mutation hazard, shrunk but not eliminated by naming fiat), no delete verb, #207/#165/#166 open for 2+ years, opaque failures (#232/#286), unmeasured scale at 10³–10⁴ objects (#111 history) — all carried to buy the lifecycle we declined.
4. **Lock-in over the 5-year horizon**: the standard's definition would live in a dialect executable only by a 15-star single-vendor app with an LTM fork and a documented app-sunsetting history — the exact risk our own research flagged as "artifact format lock-in," over exactly the horizon where it compounds.

B's advocate, to their credit, conceded most of this in the "cannot answer" round and endorsed the A-shape as the fallback.

## 6. Pre-agreed escalation triggers (consolidated from all judges)

These are falsifiable conditions, agreed now so future debates are re-opened by evidence rather than re-litigated:

| Trigger | Response |
|---|---|
| Scale measurement shows a tier can't complete inside one atomic transaction under operational Celery limits | Design Builder's shipped ChangeSet journal regains real value — re-evaluate deployment mode on measured numbers (don't hand-roll journal/resume machinery first) |
| Converge-deployed-sites-to-standard (drift *remediation*) becomes a hard requirement | **SSoT/DiffSync** — all three advocates independently conceded this identical answer; not hand-rolled diffing, not Design Builder (#207/no-delete can't converge) |
| Real structural conditionals emerge beyond fixed S/M/L (variable NFV counts, optional OOB) | Python-native logic in the library; the fixed-shape spec assumption (F8) is the premise most likely to erode — watch it |
| Governance shifts to reviewers who read YAML but not Python | B's authored-design reviewability becomes decisive (the one criterion B won outright) — reopen |
| Round-trip verifier demoted to optional | The spec loses its oracle role; unadorned A is then correct (C's own stated break condition) |
| NTC fixes #207/#165/#166, publishes deployment-mode scale measurements, AND lifecycle is promoted to a requirement | Reopen a third time, on measurements |

## 7. What survives from v1 (~1,650 of ~3,040 LOC, plus everything in docs/)

**Survives, role changed:**
- `jobs/capture/` (~950 LOC) — the walker, component differ, and capture job become the **round-trip verifier and drift auditor**. Failure mode drops from "corrupt template" to "false alarm in a report."
- The spec layer (~650 LOC): `spec.py` (validation), `params.py`/`rewrite.py` (offset/name math now evaluated in Python, not emitted as Jinja), `constants.py` (CREATION_ORDER becomes the library's execution contract), `lint.py`. Spec flips from capture-output to **authored artifact**; schema v2 adds virtualization, VC, relationship, software-version, and console-cable families.
- `render/job.py`'s validator *logic* (reference existence, LocationType chain, fingerprint drift, supernet fit, collision checks) — re-hosted as plain pre-flight functions in the shared library.
- `identity.py`'s naming policy — becomes a written rule of the authored standard (site-code-scoped names), not action-tag mechanics.
- `fixtures/build_fixture_site.py`, the composer-lab harness, the research corpus (the library's design document), and all operational guardrails.

**Deleted:** the Design Builder render arm (`render/design.py`, `render/package.py`, `escape.py`, the codegen half of `render/job.py`, action-tag half of `identity.py`) — ~1,330 LOC of DSL-satisfaction cost, with its five live SPIKE-TODOs and the unresolved High-rated identifier question. The spike gates it required die with it.

## 8. Where the original thesis was right, and where the debate amended it

**Right:** Design Builder (and SSoT) add no differentiated value at these requirements; direct ORM is definitionally maximal for the rich standard; the ecosystem's own history (NetBox #1994, core's factory-job docs, Terraformer's death) endorses purpose-built org code; AI-maintenance makes the build cheap.

**Amended:** "a custom job per deployment type, adjusted by prompts" is not the winning shape *as stated* — three specific disciplines were added by the debate and are load-bearing: (1) one shared library owns all ORM writes; tier modules are data only, CI-enforced — otherwise N divergent AI-edited jobs each re-expose the 9-tier/two-phase/update-by-name invariants; (2) an independent, machine-validated acceptance oracle (the authored spec + retained walker) — because AI provides no ground truth for its own output, verification is where the assurance burden lands when generation gets cheap; (3) the guardrail floor (stamping, teardown, dry-run, approvals, transaction/queue settings) ships in sprint one, not "later."

## 9. Proposed next steps (on sign-off)

1. Re-lock strategy: update [capture-to-design-plan.md](capture-to-design-plan.md) status header; retitle the pipeline (capture = verify stage only).
2. Spec schema v2: add the rich-model families in dependency order (days, not weeks — the analysis exists in site-data-model.md).
3. Author the standard: one spec per tier, written against your standard-deployment documentation.
4. Build the shared materialization library + thin tier jobs + teardown job (first sprint), reusing the salvaged validator logic.
5. Composer-lab verification: deploy the fixture standard, run the retained walker round-trip, exercise the hostile cases. (The lab spike survives; only its Design Builder gates die.)

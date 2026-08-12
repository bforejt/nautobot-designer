# Next session — tabled items

**Tabled by Brian, 2026-08-11:**

## Spec-bootstrap capture job

Build a job that looks at an existing site and generates the necessary spec from
it. This restores the capture walker's generation role in a form consistent with
the approved decision: the authored spec remains the system of record — capture
produces the **first draft** of a tier's spec from a representative site, which a
human then curates into the standard (rather than the spec being the captured
site verbatim).

Design idea from Brian to evaluate: drive the capture from a **Location API
query with max depth**, so the example data selection is bounded and repeatable
(deterministic subtree, same result on re-runs) rather than an unbounded ORM
tree walk.

Notes for pickup:
- The walker (jobs/capture/) already does ~90% of this; the work is (a) schema-v2
  families it doesn't yet capture (VC, VMs/clusters, relationships, software
  versions), (b) the depth-bounded/deterministic traversal question, and
  (c) emitting the draft with "curate me" markers where authored intent is
  required (parameter slots, scope decisions) instead of lint errors.
- Sequencing question to settle next session: build this before or alongside
  spec schema v2 (they share the family definitions either way).

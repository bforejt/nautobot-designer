"""The shared materialization library (docs/implementation-approach-decision.md §3).

Two-stage pipeline:

    resolve (pure)        spec + param map + seeds -> ResolvedPlan
    execute (ORM)         ResolvedPlan -> objects, atomic, stamped

The ResolvedPlan is the dry-run artifact AND the verify job's oracle:
capture(deployed site) must deep-diff clean against it.
"""

from .resolver import Seeds, resolve  # noqa: F401

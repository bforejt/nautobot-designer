"""Deploy Standard Site — Stage 4 of the pipeline, direct ORM.

resolve (pure) -> preflight (read-only ORM checks) -> execute (atomic ORM).
The resolved plan is attached to the JobResult as the dry-run/review artifact
and is the verify job's oracle.
"""

from __future__ import annotations

import json

from nautobot.apps.jobs import ChoiceVar, DryRunVar, JSONVar, ObjectVar, StringVar, Job
from nautobot.dcim.models import Location

from design_template_factory.materializer import Seeds, resolve
from design_template_factory.materializer.executor import ExecutionError, execute_plan
from design_template_factory.materializer.preflight import PreflightError, run_preflight

from ..templates_store import load_template, template_choices


class DeployStandardSite(Job):
    """Deploy a new site from a blessed template."""

    template = ChoiceVar(
        choices=template_choices(), description="Blessed template (templates/ in git)"
    )
    site_code = StringVar(
        description="Short site code embedded in device/rack names",
        regex=r"^[A-Za-z0-9]{2,12}$",
    )
    site_name = StringVar(
        description="Name of the new site location", regex=r"^[^\"']{1,100}$"
    )
    parent_location = ObjectVar(
        model=Location, description="Existing parent for the new site"
    )
    supernets = JSONVar(
        description=(
            'Seed-to-supernet mapping, e.g. {"supernet_1": "10.20.0.0/16"} — '
            "seeds are listed in the template's param-map.yaml"
        )
    )
    dryrun = DryRunVar(
        description="Resolve, validate, and execute inside a rolled-back transaction"
    )

    class Meta:
        name = "Deploy Standard Site"
        description = "Create a full site from a blessed template via the ORM"
        has_sensitive_variables = False
        is_singleton = True
        # A full-site run outlives default Celery limits (300s/600s SIGKILL).
        soft_time_limit = 1800
        time_limit = 3600
        dryrun_default = True

    def run(self, *, template, site_code, site_name, parent_location, supernets, dryrun=True):
        spec, pmap = load_template(template)
        seeds = Seeds(
            site_code=site_code,
            site_name=site_name,
            supernets=dict(supernets or {}),
            parent_location=parent_location,
        )

        plan = resolve(spec, pmap, seeds)
        plan_json = json.dumps(plan, indent=2, sort_keys=False)
        self.create_file(f"resolved-plan-{site_code.lower()}.json", plan_json)
        self.logger.info(
            "Resolved plan: %s objects across %s families",
            sum(len(v) for v in plan.values()),
            sum(1 for v in plan.values() if v),
        )

        try:
            for note in run_preflight(spec, pmap, seeds, plan):
                self.logger.info(note)
        except PreflightError as err:
            self.fail(f"Preflight failed — nothing was written: {err}")
            return

        stamp = f"{pmap.template_id}@{spec.meta.get('template_version', '0')}/{site_code}"
        try:
            report = execute_plan(plan, seeds, stamp, dryrun=dryrun, logger=self.logger)
        except ExecutionError as err:
            self.fail(f"Execution failed — transaction rolled back: {err}")
            return

        summary = {"stamp": report.stamp, "dryrun": report.dryrun, "counts": report.counts}
        self.create_file("execution-report.json", json.dumps(summary, indent=2))
        if dryrun:
            self.logger.info(
                "DRY RUN complete (all writes rolled back): %s. Review the resolved "
                "plan, then re-run with dry run unchecked.",
                report.counts,
            )
        else:
            self.logger.info(
                "Deployment complete: %s. Stamp: %s — run 'Verify Deployed Site' "
                "next; teardown is stamp-scoped if needed.",
                report.counts,
                report.stamp,
            )

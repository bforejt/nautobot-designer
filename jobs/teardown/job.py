"""Teardown Deployed Site — stamp-scoped reverse-order deletion.

Deletes exactly what a deployment stamped, nothing else. Gate this job with
Nautobot approval workflows in production; dry-run is the default.
"""

from __future__ import annotations

import json

from nautobot.apps.jobs import BooleanVar, DryRunVar, StringVar, Job

from design_template_factory.materializer.teardown import teardown


class TeardownDeployedSite(Job):
    """Delete every object carrying a deployment's provisioned_from stamp."""

    stamp = StringVar(
        description=(
            "Exact provisioned_from stamp of the deployment "
            "(e.g. 'branch-small@0/AUS01' — shown in the deploy job's report)"
        )
    )
    confirm = BooleanVar(
        default=False, description="I understand this deletes the deployed site"
    )
    dryrun = DryRunVar(description="List what would be deleted without deleting")

    class Meta:
        name = "Teardown Deployed Site"
        description = "Stamp-scoped reverse-order deletion of a deployed site"
        has_sensitive_variables = False
        is_singleton = True
        soft_time_limit = 900
        time_limit = 1800
        dryrun_default = True

    def run(self, *, stamp, confirm=False, dryrun=True):
        if not dryrun and not confirm:
            self.fail("Set 'confirm' to run a real teardown.")
            return
        report = teardown(stamp, dryrun=dryrun, logger=self.logger)
        self.create_file(
            "teardown-report.json",
            json.dumps(
                {"stamp": report.stamp, "dryrun": report.dryrun, "counts": report.counts},
                indent=2,
            ),
        )
        self.create_file("teardown-details.txt", "\n".join(report.details) + "\n")
        total = sum(report.counts.values())
        if dryrun:
            self.logger.info("DRY RUN: %s objects would be deleted: %s", total, report.counts)
        else:
            self.logger.info("Teardown complete: %s objects deleted: %s", total, report.counts)

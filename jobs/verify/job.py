"""Verify Deployed Site — Stage 5, the acceptance gate.

Re-captures the deployed site with the walker and deep-diffs it against
resolve(template, seeds). Empty diff = the deployment matches the standard.
"""

from __future__ import annotations

import json

from nautobot.apps.jobs import ChoiceVar, JSONVar, ObjectVar, StringVar, Job
from nautobot.dcim.models import Location

from design_template_factory import lint
from design_template_factory.materializer import Seeds, resolve
from design_template_factory.materializer.diffing import diff_deployment

from ..capture.walker import SiteWalker
from ..templates_store import load_template, template_choices


class VerifyDeployedSite(Job):
    """Round-trip acceptance: capture the deployed site, diff vs the template."""

    location = ObjectVar(model=Location, description="Root location of the deployed site")
    template = ChoiceVar(choices=template_choices(), description="Template it was deployed from")
    site_code = StringVar(regex=r"^[A-Za-z0-9]{2,12}$")
    supernets = JSONVar(description="The same seed mapping used at deploy time")

    class Meta:
        name = "Verify Deployed Site"
        description = "Deep-diff a deployed site against its blessed template"
        has_sensitive_variables = False
        read_only = True
        soft_time_limit = 900
        time_limit = 1800

    def run(self, *, location, template, site_code, supernets):
        spec, pmap = load_template(template)
        seeds = Seeds(
            site_code=site_code,
            site_name=location.name,
            supernets=dict(supernets or {}),
            parent_location=location.parent,
        )
        expected = resolve(spec, pmap, seeds)

        walker = SiteWalker(location, site_code)
        actual = walker.walk()
        self.create_file(
            "verify-lint.md",
            lint.render_markdown(walker.findings, title=f"Verify capture — {location.name}"),
        )

        diffs = diff_deployment(expected, actual)
        report = {
            "template": template,
            "site": location.name,
            "differences": diffs,
        }
        self.create_file("verify-report.json", json.dumps(report, indent=2))

        if diffs:
            for line in diffs[:50]:
                self.logger.warning(line)
            self.fail(
                f"Deployment does NOT match the template: {len(diffs)} difference(s) "
                "— see verify-report.json"
            )
        else:
            self.logger.info(
                "Deployment matches the template (deep-diff = 0 differences)."
            )

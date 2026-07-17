"""The Capture Site Template job (Stage 1 of the pipeline).

Read-only: walks one Location subtree and emits the site-spec JSON plus the
lint report as JobResult file artifacts. Blessing a template = reviewing the
lint report and committing the spec to the template git repository.
"""

from __future__ import annotations

import json

from nautobot.apps.jobs import BooleanVar, Job, ObjectVar, StringVar
from nautobot.dcim.models import Location

from design_template_factory import constants, lint
from design_template_factory.spec import SiteSpec

from .walker import SiteWalker

try:  # Nautobot version string for the spec _meta stamp
    from nautobot import __version__ as NAUTOBOT_VERSION
except Exception:  # pragma: no cover
    NAUTOBOT_VERSION = "unknown"


class CaptureSiteTemplate(Job):
    """Capture an existing site into a site-spec JSON template."""

    location = ObjectVar(
        model=Location,
        description="Root location of the golden site to capture",
    )
    site_code = StringVar(
        description=(
            "The site code embedded in this site's device/rack names "
            "(drives name parameterization and lint checks)"
        ),
        regex=r"^[A-Za-z0-9]{2,12}$",
    )
    allow_lint_errors = BooleanVar(
        default=False,
        description="Emit the spec even when lint finds ERROR-level problems",
    )

    class Meta:
        name = "Capture Site Template"
        description = (
            "Walk a fully-built location subtree and export it as a "
            "site-spec JSON for the design-template-factory pipeline"
        )
        has_sensitive_variables = False
        read_only = True
        soft_time_limit = 900
        time_limit = 1800

    def run(self, *, location, site_code, allow_lint_errors=False):  # noqa: D102
        walker = SiteWalker(location, site_code)
        objects = walker.walk()

        spec = SiteSpec(
            meta={
                "spec_schema": constants.SPEC_SCHEMA_VERSION,
                "template_version": "0",
                "scope_tier": constants.SCOPE_TIER_V1,
                "source": {
                    "nautobot": NAUTOBOT_VERSION,
                    "site_code": site_code,
                    "location": location.name,
                    "location_type": location.location_type.name,
                    "parent_location_type": (
                        location.parent.location_type.name if location.parent else ""
                    ),
                },
                "target": {"design_builder": constants.DESIGN_BUILDER_TARGET},
                "lint": lint.to_dicts(walker.findings),
            },
            references=walker.references(),
            objects=objects,
        )

        problems = spec.validate()
        for problem in problems:
            walker.findings.append(
                lint.Finding(severity="error", category="spec-invalid", message=problem)
            )
        spec.meta["lint"] = lint.to_dicts(walker.findings)

        report = lint.render_markdown(
            walker.findings, title=f"Capture lint report — {location.name}"
        )
        self.create_file("capture-lint-report.md", report)

        errors = [f for f in walker.findings if f.severity == "error"]
        warnings = [f for f in walker.findings if f.severity == "warning"]
        self.logger.info(
            "Capture finished: %s findings (%s errors, %s warnings)",
            len(walker.findings),
            len(errors),
            len(warnings),
        )

        if errors and not allow_lint_errors:
            self.fail(
                "Capture found ERROR-level lint findings; fix the source data or "
                "re-run with allow_lint_errors. No spec was emitted."
            )
            return

        spec_json = json.dumps(spec.to_dict(), indent=2, sort_keys=False) + "\n"
        self.create_file(f"site-spec-{site_code.lower()}.json", spec_json)
        self.logger.info(
            "Spec emitted with %s objects across %s families; review the lint "
            "report, then commit the spec to the template repository and run "
            "`dtf propose-params`.",
            sum(len(v) for v in spec.objects.values()),
            sum(1 for v in spec.objects.values() if v),
        )

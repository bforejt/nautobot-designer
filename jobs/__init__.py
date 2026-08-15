"""Jobs package for nautobot-design-template-factory.

Four jobs implement the approved architecture
(docs/implementation-approach-decision.md):

    Capture Site Template   -> draft spec from an existing site (curate in git)
    Deploy Standard Site    -> blessed template + seeds -> new site (direct ORM)
    Verify Deployed Site    -> re-capture + deep-diff vs template (acceptance)
    Teardown Deployed Site  -> stamp-scoped reverse-order deletion

Load this repository into Nautobot as a Git repository data source providing
*jobs*. The design_template_factory library must be pip-installed in the
Nautobot environment (see docs/lab-runbook.md).
"""

from nautobot.apps.jobs import register_jobs

from .capture.job import CaptureSiteTemplate
from .deploy.job import DeployStandardSite
from .teardown.job import TeardownDeployedSite
from .verify.job import VerifyDeployedSite

register_jobs(
    CaptureSiteTemplate,
    DeployStandardSite,
    VerifyDeployedSite,
    TeardownDeployedSite,
)

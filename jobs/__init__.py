"""Jobs package for nautobot-design-template-factory.

Load this repository into Nautobot as a Git repository data source providing
*jobs*. The design_template_factory library must be pip-installed in the
Nautobot environment (e.g. add it to the composer image requirements).
"""

from nautobot.apps.jobs import register_jobs

from .capture.job import CaptureSiteTemplate

register_jobs(CaptureSiteTemplate)

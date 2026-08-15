"""Template discovery for jobs running from the git data source checkout.

A template is a directory under <repo>/templates/ containing:
    spec.json       the blessed site-spec (system of record)
    param-map.yaml  the reviewed parameter map
"""

from __future__ import annotations

from pathlib import Path

from design_template_factory.params import ParamMap
from design_template_factory.spec import SiteSpec

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


def template_choices() -> list[tuple[str, str]]:
    if not TEMPLATES_DIR.is_dir():
        return []
    return [
        (path.name, path.name)
        for path in sorted(TEMPLATES_DIR.iterdir())
        if (path / "spec.json").is_file() and (path / "param-map.yaml").is_file()
    ]


def load_template(template_id: str) -> tuple[SiteSpec, ParamMap]:
    root = TEMPLATES_DIR / template_id
    spec = SiteSpec.load(root / "spec.json")
    pmap = ParamMap.load(root / "param-map.yaml")
    return spec, pmap

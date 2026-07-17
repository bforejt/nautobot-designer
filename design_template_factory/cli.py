"""dtf — the offline half of the pipeline (templatize + render + validate).

    dtf validate <spec.json>
    dtf propose-params <spec.json> -o param-map.yaml
    dtf render <spec.json> <param-map.yaml> -o <package-dir>
"""

from __future__ import annotations

import argparse
import sys

from . import params as params_mod
from .render import render_package
from .render.design import RenderError
from .rewrite import RewriteError
from .spec import SiteSpec, SpecError


def _cmd_validate(args: argparse.Namespace) -> int:
    spec = SiteSpec.load(args.spec)
    problems = spec.validate()
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    print(f"OK: spec {args.spec} is structurally valid")
    return 0


def _cmd_propose(args: argparse.Namespace) -> int:
    spec = SiteSpec.load(args.spec)
    proposal = params_mod.propose(spec)
    proposal.save(args.output)
    print(f"Wrote proposed parameter map to {args.output}")
    if proposal.notes:
        print("Review notes (also embedded in the file):")
        for note in proposal.notes:
            print(f"  - {note}")
    print("Review and commit the map before rendering — it is the template's intent.")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    spec = SiteSpec.load(args.spec)
    pmap = params_mod.ParamMap.load(args.param_map)
    if pmap.source_site_code != spec.source_site_code:
        print(
            f"ERROR: param map is for site {pmap.source_site_code!r} but spec "
            f"was captured from {spec.source_site_code!r}",
            file=sys.stderr,
        )
        return 1
    written = render_package(spec, pmap, args.output)
    print(f"Wrote design package ({len(written)} files) under {args.output}/jobs/")
    for path in written:
        print(f"  {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dtf", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="structurally validate a captured spec")
    validate.add_argument("spec")
    validate.set_defaults(func=_cmd_validate)

    propose = sub.add_parser("propose-params", help="propose a parameter map from a spec")
    propose.add_argument("spec")
    propose.add_argument("-o", "--output", default="param-map.yaml")
    propose.set_defaults(func=_cmd_propose)

    render = sub.add_parser("render", help="render a Design Builder design package")
    render.add_argument("spec")
    render.add_argument("param_map")
    render.add_argument("-o", "--output", default="rendered")
    render.set_defaults(func=_cmd_render)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (SpecError, params_mod.ParamMapError, RenderError, RewriteError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

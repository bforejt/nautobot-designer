"""dtf — offline template tooling.

    dtf validate <spec.json>                     structural validation
    dtf propose-params <spec.json> -o map.yaml   draft a parameter map
    dtf resolve <spec.json> <map.yaml> --site-code X --site-name N \
        --supernet seed=cidr [-o plan.json]      preview a resolved plan
"""

from __future__ import annotations

import argparse
import json
import sys

from . import params as params_mod
from .materializer.resolver import ResolveError, Seeds, resolve
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
    for note in proposal.notes:
        print(f"  - {note}")
    print("Review and commit the map before blessing — it is the template's intent.")
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    spec = SiteSpec.load(args.spec)
    pmap = params_mod.ParamMap.load(args.param_map)
    supernets = dict(pair.split("=", 1) for pair in args.supernet or [])
    plan = resolve(
        spec,
        pmap,
        Seeds(site_code=args.site_code, site_name=args.site_name, supernets=supernets),
    )
    output = json.dumps(plan, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(output + "\n")
        print(f"Wrote resolved plan to {args.output}")
    else:
        print(output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dtf", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="structurally validate a spec")
    validate.add_argument("spec")
    validate.set_defaults(func=_cmd_validate)

    propose = sub.add_parser("propose-params", help="propose a parameter map from a spec")
    propose.add_argument("spec")
    propose.add_argument("-o", "--output", default="param-map.yaml")
    propose.set_defaults(func=_cmd_propose)

    resolve_cmd = sub.add_parser("resolve", help="preview a resolved deployment plan")
    resolve_cmd.add_argument("spec")
    resolve_cmd.add_argument("param_map")
    resolve_cmd.add_argument("--site-code", required=True)
    resolve_cmd.add_argument("--site-name", required=True)
    resolve_cmd.add_argument(
        "--supernet", action="append", metavar="SEED=CIDR", help="repeatable"
    )
    resolve_cmd.add_argument("-o", "--output")
    resolve_cmd.set_defaults(func=_cmd_resolve)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (SpecError, params_mod.ParamMapError, ResolveError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

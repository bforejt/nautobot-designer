"""Package assembly, generated job module, and CLI round trip."""

import json

import yaml

from design_template_factory import cli
from design_template_factory.render.job import render_job_module
from design_template_factory.render.package import render_package


def test_generated_job_module_is_valid_python(spec, param_map):
    source = render_job_module(
        spec, param_map, ["designs/0001_site.yaml.j2"], has_cabling=True
    )
    compile(source, "<generated>", "exec")  # syntax check only (imports need Nautobot)
    assert "${" not in source, "unsubstituted template placeholder left in codegen"
    assert "class BranchSmall(DesignJob):" in source
    assert "supernet_1 = IPNetworkVar" in source
    assert "CableConnectionExtension" in source
    assert "is_singleton = True" in source
    assert "soft_time_limit" in source
    # Embedded references survive the codegen round trip.
    assert '"template_fingerprint"' in source


def test_generated_job_module_without_cabling(spec, param_map):
    source = render_job_module(
        spec, param_map, ["designs/0001_site.yaml.j2"], has_cabling=False
    )
    compile(source, "<generated>", "exec")
    assert "CableConnectionExtension" not in source


def test_render_package_layout(spec, param_map, tmp_path):
    written = render_package(spec, param_map, tmp_path)
    relative = sorted(str(p.relative_to(tmp_path)) for p in written)
    assert relative == [
        "jobs/__init__.py",
        "jobs/branch_small/README.md",
        "jobs/branch_small/__init__.py",
        "jobs/branch_small/designs/0001_site.yaml.j2",
        "jobs/branch_small/designs/0002_primary_ips.yaml.j2",
        "jobs/branch_small/designs/0003_cabling.yaml.j2",
    ]
    jobs_init = (tmp_path / "jobs" / "__init__.py").read_text()
    assert "register_jobs(BranchSmall)" in jobs_init

    readme = (tmp_path / "jobs" / "branch_small" / "README.md").read_text()
    assert "provisioned_from" in readme
    assert "non-site-coded-name" in readme  # lint replay
    assert "#207" in readme


def test_cli_full_round_trip(spec, tmp_path, capsys):
    spec_path = tmp_path / "spec.json"
    spec.save(spec_path)

    assert cli.main(["validate", str(spec_path)]) == 0

    map_path = tmp_path / "param-map.yaml"
    assert cli.main(["propose-params", str(spec_path), "-o", str(map_path)]) == 0
    proposal = yaml.safe_load(map_path.read_text())
    assert proposal["template"]["source_site_code"] == "DAL01"

    out_dir = tmp_path / "rendered"
    assert cli.main(["render", str(spec_path), str(map_path), "-o", str(out_dir)]) == 0
    assert (out_dir / "jobs" / "dal01_template" / "designs" / "0001_site.yaml.j2").exists()


def test_cli_render_rejects_mismatched_map(spec, param_map, tmp_path, capsys):
    spec_path = tmp_path / "spec.json"
    spec.save(spec_path)
    param_map.source_site_code = "OTHER"
    map_path = tmp_path / "param-map.yaml"
    param_map.save(map_path)
    assert cli.main(["render", str(spec_path), str(map_path), "-o", str(tmp_path / "x")]) == 1
    assert "captured from" in capsys.readouterr().err


def test_spec_meta_lint_reaches_readme(spec_dict, param_map, tmp_path):
    from design_template_factory.spec import SiteSpec

    spec_dict["_meta"]["lint"].append(
        {
            "severity": "warning",
            "category": "component-removal",
            "message": "Ethernet4 removed from DAL01-SW2; clones WILL have it",
            "obj": "DAL01-SW2",
        }
    )
    render_package(SiteSpec.from_dict(spec_dict), param_map, tmp_path)
    readme = (tmp_path / "jobs" / "branch_small" / "README.md").read_text()
    assert "component-removal" in readme


def test_spec_json_is_stable(spec, tmp_path):
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    spec.save(first)
    spec.save(second)
    assert json.loads(first.read_text()) == json.loads(second.read_text())
    assert first.read_text() == second.read_text()


def test_design_files_meta_matches_layout(spec, param_map, tmp_path):
    """Review finding: Meta.design_files must point at real on-disk paths
    relative to the generated job module's directory."""
    import re

    render_package(spec, param_map, tmp_path)
    package_dir = tmp_path / "jobs" / "branch_small"
    source = (package_dir / "__init__.py").read_text()
    match = re.search(r"design_files = (\[[^\]]*\])", source)
    assert match, "design_files not found in generated Meta"
    for path in eval(match.group(1)):  # noqa: S307 - our own generated literal
        assert (package_dir / path).exists(), f"design file missing: {path}"


def test_render_package_is_deterministic(spec, param_map, tmp_path):
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    render_package(spec, param_map, a_dir)
    render_package(spec, param_map, b_dir)
    a_files = sorted(p.relative_to(a_dir) for p in a_dir.rglob("*") if p.is_file())
    b_files = sorted(p.relative_to(b_dir) for p in b_dir.rglob("*") if p.is_file())
    assert a_files == b_files
    for rel in a_files:
        assert (a_dir / rel).read_bytes() == (b_dir / rel).read_bytes(), rel


def test_generated_collision_validator_content(spec, param_map):
    source = render_job_module(spec, param_map, ["designs/0001_site.yaml.j2"], False)
    assert "validate_no_name_collisions" in source
    assert "'{site_code}-SW1'" in source  # devices template
    assert "'{site_code}-vlans'" in source  # vlan group template
    assert "content_types" in source  # gating check present


def test_cli_validate_fails_on_broken_spec(spec_dict, tmp_path, capsys):
    import json as json_mod

    from design_template_factory import cli as cli_mod

    spec_dict["ip_assignments"][0]["device"] = "GHOST"
    path = tmp_path / "broken.json"
    path.write_text(json_mod.dumps(spec_dict))
    assert cli_mod.main(["validate", str(path)]) == 1
    assert "GHOST" in capsys.readouterr().err

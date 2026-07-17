import pytest

from design_template_factory.escape import Placeholder, escape_literal, escape_tree
from design_template_factory.rewrite import (
    RewriteError,
    address_offset_expr,
    apply_name_patterns,
    compute_roots,
    find_root,
    is_site_coded,
    network_offset_expr,
)
from design_template_factory.spec import SiteSpec, SpecError


class TestSpec:
    def test_round_trip(self, spec, tmp_path):
        path = tmp_path / "spec.json"
        spec.save(path)
        reloaded = SiteSpec.load(path)
        assert reloaded.to_dict() == spec.to_dict()

    def test_validate_clean(self, spec):
        assert spec.validate() == []

    def test_schema_mismatch_rejected(self, spec_dict):
        spec_dict["_meta"]["spec_schema"] = "0.9"
        with pytest.raises(SpecError, match="re-capture"):
            SiteSpec.from_dict(spec_dict)

    def test_unknown_key_rejected(self, spec_dict):
        spec_dict["gadgets"] = []
        with pytest.raises(SpecError, match="unknown keys"):
            SiteSpec.from_dict(spec_dict)

    def test_dangling_assignment_detected(self, spec_dict):
        spec_dict["ip_assignments"][0]["device"] = "GHOST"
        problems = SiteSpec.from_dict(spec_dict).validate()
        assert any("GHOST" in p for p in problems)

    def test_missing_fingerprint_detected(self, spec_dict):
        del spec_dict["_references"]["device_types"][0]["template_fingerprint"]
        problems = SiteSpec.from_dict(spec_dict).validate()
        assert any("fingerprint" in p for p in problems)


class TestEscape:
    def test_literal_delimiters_neutralized(self):
        escaped = escape_literal("boom {{ malicious }} {% tag %} {# c #}")
        assert "{{ '{{' }}" in escaped
        assert "{{ '{%' }}" in escaped
        assert "{{ '{#' }}" in escaped

    def test_placeholder_untouched(self):
        assert escape_literal(Placeholder("{{ site_code }}")) == "{{ site_code }}"

    def test_tree_escapes_nested(self):
        tree = escape_tree({"k": ["{{ x }}", Placeholder("{{ ok }}")]})
        assert tree["k"][0] == "{{ '{{' }} x {{ '}}' }}"
        assert tree["k"][1] == "{{ ok }}"


class TestRewrite:
    def test_name_pattern_yields_placeholder(self):
        result = apply_name_patterns("DAL01-SW1", [{"pattern": "DAL01", "replace": "{{ site_code }}"}])
        assert isinstance(result, Placeholder)
        assert result == "{{ site_code }}-SW1"
        assert is_site_coded(result)

    def test_unmatched_name_is_plain(self):
        result = apply_name_patterns("R2", [{"pattern": "DAL01", "replace": "{{ site_code }}"}])
        assert not isinstance(result, Placeholder)
        assert not is_site_coded(result)

    def test_network_offset_expr(self):
        expr = network_offset_expr("10.10.20.0/24", "10.10.0.0/16", "supernet_1")
        assert expr == "{{ supernet_1 | string | network_offset('0.0.20.0/24') }}"

    def test_address_offset_expr(self):
        expr = address_offset_expr("10.10.0.11/24", "10.10.0.0/16", "supernet_1")
        assert expr == "{{ supernet_1 | string | network_offset('0.0.0.11/24') }}"

    def test_ipv6_offset(self):
        expr = network_offset_expr("2001:db8:0:20::/64", "2001:db8::/48", "supernet_v6")
        assert "network_offset('" in expr and expr.endswith("/64') }}")

    def test_offset_outside_root_raises(self):
        with pytest.raises(RewriteError):
            network_offset_expr("192.168.0.0/24", "10.0.0.0/8", "seed")

    def test_find_root_most_specific(self):
        roots = ["10.0.0.0/8", "10.10.0.0/16"]
        assert find_root("10.10.5.0/24", roots) == "10.10.0.0/16"
        assert find_root("10.99.0.0/24", roots) == "10.0.0.0/8"
        assert find_root("192.168.0.0/24", roots) is None

    def test_compute_roots(self):
        roots = compute_roots(["10.10.0.0/24", "10.10.10.0/24", "10.10.0.0/16"])
        assert roots == ["10.10.0.0/16"]

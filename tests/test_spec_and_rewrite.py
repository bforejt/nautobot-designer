import pytest

from design_template_factory.rewrite import (
    RewriteError,
    compute_roots,
    find_root,
    rebase_address,
    rebase_network,
    resolve_name,
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

    def test_unknown_location_path_detected(self, spec_dict):
        spec_dict["racks"][0]["location"] = "DAL01/Basement"
        problems = SiteSpec.from_dict(spec_dict).validate()
        assert any("Basement" in p for p in problems)


class TestRewrite:
    PATTERNS = [{"pattern": "(?i)DAL01", "replace": "{{ site_code }}"}]

    def test_resolve_name_substitutes_seed(self):
        assert resolve_name("DAL01-SW1", self.PATTERNS, "AUS01") == "AUS01-SW1"
        assert resolve_name("dal01-r1", self.PATTERNS, "AUS01") == "AUS01-r1"

    def test_resolve_name_untouched_when_no_match(self):
        assert resolve_name("R2", self.PATTERNS, "AUS01") == "R2"

    def test_rebase_network(self):
        assert rebase_network("10.10.20.0/24", "10.10.0.0/16", "10.20.0.0/16") == "10.20.20.0/24"

    def test_rebase_address(self):
        assert rebase_address("10.10.0.11/24", "10.10.0.0/16", "10.20.0.0/16") == "10.20.0.11/24"

    def test_rebase_ipv6(self):
        assert (
            rebase_network("2001:db8:0:20::/64", "2001:db8::/48", "2001:db9::/48")
            == "2001:db9:0:20::/64"
        )

    def test_rebase_outside_root_raises(self):
        with pytest.raises(RewriteError):
            rebase_network("192.168.0.0/24", "10.0.0.0/8", "172.16.0.0/12")

    def test_rebase_smaller_target_raises(self):
        with pytest.raises(RewriteError, match="smaller"):
            rebase_network("10.10.20.0/24", "10.10.0.0/16", "10.20.0.0/20")

    def test_find_root_most_specific(self):
        roots = ["10.0.0.0/8", "10.10.0.0/16"]
        assert find_root("10.10.5.0/24", roots) == "10.10.0.0/16"
        assert find_root("10.99.0.0/24", roots) == "10.0.0.0/8"
        assert find_root("192.168.0.0/24", roots) is None

    def test_compute_roots(self):
        assert compute_roots(["10.10.0.0/24", "10.10.10.0/24", "10.10.0.0/16"]) == [
            "10.10.0.0/16"
        ]

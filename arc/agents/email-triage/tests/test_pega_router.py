"""Tests for the Pega case-type schema registry.

Covers three layers:

  1. Snapshot tests against the two shipped schemas — verify a realistic
     ``triage_data`` produces the expected Pega payload shape, including
     transforms, defaults, and required-field enforcement.
  2. Loader behaviour — directory walks, underscore-prefix files,
     duplicate case_type detection, malformed YAML.
  3. Transform dispatch — every supported transform + the unknown-name
     error path.

Tests use ``tmp_path`` for the loader tests so they're isolated from
the shipped YAML files. The snapshot tests load the real shipped files
to catch regressions when schemas change shape.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

# The agent directory contains a hyphen (`email-triage`) so it isn't
# importable as a Python package. Load the router module by path —
# mirrors how arc's HarnessBuilder resolves agent code at runtime.
_AGENT_DIR = Path(__file__).parent.parent


def _load_router_module():
    spec = importlib.util.spec_from_file_location(
        "_pega_router_under_test",
        _AGENT_DIR / "pega_router.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_router = _load_router_module()
PegaSchemaError    = _router.PegaSchemaError
PegaSchemaRegistry = _router.PegaSchemaRegistry

SCHEMAS_DIR = _AGENT_DIR / "pega_schemas"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def shipped_registry() -> PegaSchemaRegistry:
    """Loads the actual shipped schemas (distribution / loan_hardship / sponsor_inquiry)."""
    return PegaSchemaRegistry(SCHEMAS_DIR)


def _write_schema(dir_: Path, name: str, body: dict) -> Path:
    """Helper — write a schema YAML to a temp dir."""
    path = dir_ / f"{name}.yaml"
    path.write_text(yaml.dump(body))
    return path


def _minimal_schema(case_type: str, **overrides) -> dict:
    """A minimum-shape valid schema, easy to extend per test."""
    return {
        "case_type":      case_type,
        "pega_class":     f"ITSM-Work-{case_type.title()}",
        "schema_version": "1.0",
        "mapping":        {"x": "X"},
        **overrides,
    }


# ── 1. Snapshot tests against shipped schemas ──────────────────────────────


class TestShippedDistributionRequest:
    def test_full_payload_shape(self, shipped_registry: PegaSchemaRegistry):
        triage_data = {
            "participant_id":         "P-12345",
            "plan_id":                "PLAN-9001",
            "distribution_subtype":   "rollover",
            "amount_requested":       4200.555,    # round(2) → 4200.56
            "request_date":           "2026-04-30",
            "destination_institution": "Fidelity",
            "destination_account":    "999-888-777",
            "tax_withholding_pct":    20.0,
            "requestor":              {"email": "alice@example.com", "name": "Alice Rollover"},
            "routing":                {"team": "distributions-standard"},
            "triage":                 {"severity": "S4"},
        }

        out = shipped_registry.map("distribution", triage_data)

        assert out["caseTypeID"]     == "ITSM-Work-DistributionRequest"
        assert out["schema_version"] == "0.1.0-placeholder"

        c = out["content"]
        # Mappings
        assert c["Content"]["D_Participant"]["ParticipantID"]               == "P-12345"
        assert c["Content"]["D_Plan"]["PlanID"]                              == "PLAN-9001"
        assert c["Content"]["D_DistributionDetails"]["DistributionType"]     == "rollover"
        assert c["Content"]["D_DistributionDetails"]["AmountRequested"]      == 4200.56
        assert c["Content"]["D_DistributionDetails"]["RequestDate"]          == "2026-04-30"
        assert c["Content"]["D_Rollover"]["DestinationInstitution"]          == "Fidelity"
        assert c["Content"]["D_TaxElection"]["FederalWithholdingPct"]        == 20.0
        assert c["Content"]["D_Requestor"]["Email"]                          == "alice@example.com"
        assert c["pyAssignedToOperator"]                                     == "distributions-standard"
        # Defaults
        assert c["pyStatusWork"] == "Open"
        assert c["pyOrigin"]     == "arc-email-triage"
        assert c["Content"]["D_DistributionDetails"]["SourceOfFunds"] == "401k_pretax"

    def test_missing_required_field_fails(
        self, shipped_registry: PegaSchemaRegistry
    ):
        triage_data = {
            "participant_id":  "P-1",
            "plan_id":         "PLAN-9001",
            # distribution_subtype + request_date missing
        }
        with pytest.raises(PegaSchemaError, match="DistributionType|RequestDate"):
            shipped_registry.map("distribution", triage_data)


class TestShippedLoanHardshipRequest:
    def test_loan_payload_shape(self, shipped_registry: PegaSchemaRegistry):
        triage_data = {
            "participant_id":         "P-44210",
            "plan_id":                "PLAN-9001",
            "request_subtype":        "Loan",                   # → lower → "loan"
            "amount_requested":       12500.0,
            "loan_term_months":       60,
            "loan_purpose":           "personal",
            "request_date":           "2026-04-30",
            "documentation_provided": True,
            "requestor":              {"email": "carol@example.com", "name": "Carol Borrower"},
            "routing":                {"team": "loans-standard"},
            "triage":                 {"severity": "S4"},
        }

        out = shipped_registry.map("loan_hardship", triage_data)

        c = out["content"]
        assert c["Content"]["D_Participant"]["ParticipantID"]      == "P-44210"
        assert c["Content"]["D_LoanHardship"]["RequestType"]       == "loan"      # lower transform
        assert c["Content"]["D_LoanHardship"]["AmountRequested"]   == 12500.00
        assert c["Content"]["D_LoanHardship"]["LoanTermMonths"]    == 60
        assert c["Content"]["D_LoanHardship"]["HasSupportingDocuments"] is True
        assert c["Content"]["D_LoanHardship"]["RequiresSubstantiationReview"] is True

    def test_hardship_payload_shape(self, shipped_registry: PegaSchemaRegistry):
        triage_data = {
            "participant_id":         "P-30015",
            "plan_id":                "PLAN-9001",
            "request_subtype":        "hardship",
            "hardship_category":      "MEDICAL",                 # → lower → "medical"
            "amount_requested":       18000.0,
            "reason_text":            "Spouse hospitalised; out-of-pocket medical bills",
            "documentation_provided": True,
            "request_date":           "2026-04-28",
            "requestor":              {"email": "dan@example.com", "name": "Dan Medical"},
            "routing":                {"team": "hardship-review"},
            "triage":                 {"severity": "S2"},
        }

        out = shipped_registry.map("loan_hardship", triage_data)
        c = out["content"]

        assert c["Content"]["D_LoanHardship"]["RequestType"]       == "hardship"
        assert c["Content"]["D_LoanHardship"]["HardshipCategory"]  == "medical"   # lower transform
        assert c["Content"]["D_LoanHardship"]["RequiresSubstantiationReview"] is True
        assert c["pyAssignedToOperator"] == "hardship-review"

    def test_missing_amount_fails(
        self, shipped_registry: PegaSchemaRegistry
    ):
        triage_data = {
            "participant_id":  "P-1",
            "plan_id":         "PLAN-1",
            "request_subtype": "loan",
            "request_date":    "2026-04-30",
            # amount_requested missing
        }
        with pytest.raises(PegaSchemaError, match="AmountRequested"):
            shipped_registry.map("loan_hardship", triage_data)


class TestShippedSponsorInquiry:
    def test_compliance_inquiry_shape(self, shipped_registry: PegaSchemaRegistry):
        triage_data = {
            "sponsor_id":         "SPONSOR-ACME",
            "sponsor_company":    "Acme Industries Inc.",
            "plan_id":            "PLAN-9001",
            "inquiry_category":   "COMPLIANCE",                # → lower → "compliance"
            "inquiry_summary":    "ADP test failure — need ERISA guidance",
            "related_filing":     "5500",
            "request_date":       "2026-04-30",
            "requestor":          {
                "email": "compliance@acme-employer.com",
                "name":  "Acme Compliance Team",
                "role":  "sponsor_compliance",
            },
            "routing":            {"team": "erisa-compliance"},
            "triage":             {"severity": "S2"},
        }

        out = shipped_registry.map("sponsor_inquiry", triage_data)
        c = out["content"]

        assert c["Content"]["D_Sponsor"]["SponsorID"]         == "SPONSOR-ACME"
        assert c["Content"]["D_Sponsor"]["CompanyName"]       == "Acme Industries Inc."
        assert c["Content"]["D_Plan"]["PlanID"]               == "PLAN-9001"
        assert c["Content"]["D_Inquiry"]["Category"]          == "compliance"  # lower transform
        assert c["Content"]["D_Inquiry"]["RelatedFiling"]     == "5500"        # upper transform on already-uppercase
        assert c["pyAssignedToOperator"]                      == "erisa-compliance"

    def test_missing_summary_fails(
        self, shipped_registry: PegaSchemaRegistry
    ):
        triage_data = {
            "sponsor_id":       "SPONSOR-1",
            "plan_id":          "PLAN-9001",
            "inquiry_category": "general",
            # inquiry_summary missing
        }
        with pytest.raises(PegaSchemaError, match="Summary"):
            shipped_registry.map("sponsor_inquiry", triage_data)


class TestShippedRegistryBasics:
    def test_three_case_types_registered(self, shipped_registry: PegaSchemaRegistry):
        assert sorted(shipped_registry.case_types()) == [
            "distribution",
            "loan_hardship",
            "sponsor_inquiry",
        ]

    def test_has(self, shipped_registry: PegaSchemaRegistry):
        assert shipped_registry.has("distribution")
        assert shipped_registry.has("loan_hardship")
        assert shipped_registry.has("sponsor_inquiry")
        assert not shipped_registry.has("auto")  # legacy schema removed

    def test_schema_introspection(self, shipped_registry: PegaSchemaRegistry):
        s = shipped_registry.schema("distribution")
        assert s["case_type"]      == "distribution"
        assert s["pega_class"]     == "ITSM-Work-DistributionRequest"
        assert s["schema_version"] == "0.1.0-placeholder"
        assert "mapping" in s


# ── 2. Loader behaviour ───────────────────────────────────────────────────


class TestLoader:
    def test_missing_dir_raises(self):
        with pytest.raises(PegaSchemaError, match="not found"):
            PegaSchemaRegistry("/nonexistent/path/that/does/not/exist")

    def test_underscore_files_ignored(self, tmp_path: Path):
        _write_schema(tmp_path, "_template", _minimal_schema("ignored"))
        _write_schema(tmp_path, "real", _minimal_schema("real"))
        reg = PegaSchemaRegistry(tmp_path)
        assert reg.case_types() == ["real"]

    def test_duplicate_case_type_rejected(self, tmp_path: Path):
        _write_schema(tmp_path, "a", _minimal_schema("dup"))
        _write_schema(tmp_path, "b", _minimal_schema("dup"))
        with pytest.raises(PegaSchemaError, match="Duplicate case_type"):
            PegaSchemaRegistry(tmp_path)

    @pytest.mark.parametrize("missing_key", [
        "case_type", "pega_class", "schema_version", "mapping",
    ])
    def test_missing_required_top_level_key_rejected(
        self, tmp_path: Path, missing_key: str,
    ):
        body = _minimal_schema("x")
        del body[missing_key]
        _write_schema(tmp_path, "x", body)
        with pytest.raises(PegaSchemaError, match=missing_key):
            PegaSchemaRegistry(tmp_path)

    def test_empty_mapping_rejected(self, tmp_path: Path):
        body = _minimal_schema("x", mapping={})
        _write_schema(tmp_path, "x", body)
        with pytest.raises(PegaSchemaError, match="cannot be empty"):
            PegaSchemaRegistry(tmp_path)

    def test_non_dict_top_level_rejected(self, tmp_path: Path):
        (tmp_path / "bad.yaml").write_text("- this is a list, not a dict\n")
        with pytest.raises(PegaSchemaError, match="must be a mapping"):
            PegaSchemaRegistry(tmp_path)

    def test_malformed_yaml_rejected(self, tmp_path: Path):
        (tmp_path / "bad.yaml").write_text("key: : :\n")
        with pytest.raises(PegaSchemaError, match="Failed to parse"):
            PegaSchemaRegistry(tmp_path)


# ── 3. Mapping behaviour ──────────────────────────────────────────────────


class TestMapping:
    def test_unknown_case_type_lists_known_ones(
        self, shipped_registry: PegaSchemaRegistry,
    ):
        with pytest.raises(PegaSchemaError) as exc:
            shipped_registry.map("beneficiary_update", {})
        # The error message lists what *is* registered to help the caller.
        msg = str(exc.value)
        assert "distribution"     in msg
        assert "loan_hardship"    in msg
        assert "sponsor_inquiry"  in msg
        assert "beneficiary_update" in msg

    def test_defaults_applied(self, tmp_path: Path):
        _write_schema(tmp_path, "t", _minimal_schema(
            "t",
            defaults={"pyStatusWork": "Open", "pyOrigin": "agent"},
        ))
        reg = PegaSchemaRegistry(tmp_path)
        out = reg.map("t", {"x": "v"})
        assert out["content"]["pyStatusWork"] == "Open"
        assert out["content"]["pyOrigin"]     == "agent"
        assert out["content"]["X"]            == "v"

    def test_mapping_overrides_default_when_value_present(self, tmp_path: Path):
        _write_schema(tmp_path, "t", _minimal_schema(
            "t",
            mapping={"status": "pyStatusWork"},
            defaults={"pyStatusWork": "Open"},
        ))
        reg = PegaSchemaRegistry(tmp_path)
        out = reg.map("t", {"status": "Resolved"})
        # Mapping value wins.
        assert out["content"]["pyStatusWork"] == "Resolved"

    def test_absent_input_field_does_not_overwrite_default(self, tmp_path: Path):
        _write_schema(tmp_path, "t", _minimal_schema(
            "t",
            mapping={"status": "pyStatusWork"},
            defaults={"pyStatusWork": "Open"},
        ))
        reg = PegaSchemaRegistry(tmp_path)
        out = reg.map("t", {})  # no `status` in triage data
        # Default survives because mapping skipped (None).
        assert out["content"]["pyStatusWork"] == "Open"

    def test_dotted_path_traversal(self, tmp_path: Path):
        _write_schema(tmp_path, "t", _minimal_schema(
            "t",
            mapping={"a.b.c": "Outer.Inner.Deep"},
        ))
        reg = PegaSchemaRegistry(tmp_path)
        out = reg.map("t", {"a": {"b": {"c": "value"}}})
        assert out["content"]["Outer"]["Inner"]["Deep"] == "value"

    def test_path_conflict_raises(self, tmp_path: Path):
        # Two mappings collide on the same parent: one wants `Foo` to be
        # a string, the other wants it to be a parent dict.
        _write_schema(tmp_path, "t", _minimal_schema(
            "t",
            mapping={
                "first":  "Foo",        # sets Foo to scalar
                "second": "Foo.Bar",    # wants Foo to be a dict
            },
        ))
        reg = PegaSchemaRegistry(tmp_path)
        with pytest.raises(PegaSchemaError, match="conflict"):
            reg.map("t", {"first": "scalar", "second": "deep-value"})


# ── 4. Transforms ─────────────────────────────────────────────────────────


class TestTransforms:
    def test_round_with_digits(self, tmp_path: Path):
        _write_schema(tmp_path, "t", _minimal_schema(
            "t",
            mapping={"amount": "Total"},
            transforms={"amount": {"type": "round", "digits": 2}},
        ))
        reg = PegaSchemaRegistry(tmp_path)
        out = reg.map("t", {"amount": 1.234567})
        assert out["content"]["Total"] == 1.23

    def test_round_default_digits_zero(self, tmp_path: Path):
        _write_schema(tmp_path, "t", _minimal_schema(
            "t",
            mapping={"amount": "Total"},
            transforms={"amount": {"type": "round"}},
        ))
        reg = PegaSchemaRegistry(tmp_path)
        out = reg.map("t", {"amount": 9.7})
        assert out["content"]["Total"] == 10

    def test_upper_short_form(self, tmp_path: Path):
        _write_schema(tmp_path, "t", _minimal_schema(
            "t",
            mapping={"name": "Name"},
            transforms={"name": "upper"},  # short form
        ))
        reg = PegaSchemaRegistry(tmp_path)
        out = reg.map("t", {"name": "alice"})
        assert out["content"]["Name"] == "ALICE"

    def test_lower_long_form(self, tmp_path: Path):
        _write_schema(tmp_path, "t", _minimal_schema(
            "t",
            mapping={"email": "Email"},
            transforms={"email": {"type": "lower"}},
        ))
        reg = PegaSchemaRegistry(tmp_path)
        out = reg.map("t", {"email": "USER@EXAMPLE.COM"})
        assert out["content"]["Email"] == "user@example.com"

    def test_date_iso8601_passthrough(self, tmp_path: Path):
        _write_schema(tmp_path, "t", _minimal_schema(
            "t",
            mapping={"d": "Date"},
            transforms={"d": "date_iso8601"},
        ))
        reg = PegaSchemaRegistry(tmp_path)
        out = reg.map("t", {"d": "2026-04-22T00:00:00Z"})
        assert out["content"]["Date"] == "2026-04-22T00:00:00Z"

    def test_unknown_transform_raises(self, tmp_path: Path):
        _write_schema(tmp_path, "t", _minimal_schema(
            "t",
            mapping={"x": "X"},
            transforms={"x": {"type": "made-up-transform"}},
        ))
        reg = PegaSchemaRegistry(tmp_path)
        with pytest.raises(PegaSchemaError, match="Unknown transform"):
            reg.map("t", {"x": 1})

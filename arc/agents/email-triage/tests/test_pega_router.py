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
    """Loads the actual shipped schemas (auto + property)."""
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


class TestShippedAutoClaim:
    def test_full_payload_shape(self, shipped_registry: PegaSchemaRegistry):
        triage_data = {
            "policy_number": "POL-12345",
            "claim_amount":  4200.555,         # transforms.round(2) → 4200.56
            "incident_date": "2026-04-22",
            "vehicle_vin":   "1HGCM82633A004352",
            "driver_id":     "DRV-9001",
            "claimant":      {"email": "j@example.com", "name": "Jane Doe"},
            "routing":       {"team": "auto-senior-adjusters"},
            "triage":        {"severity": "S2"},
        }

        out = shipped_registry.map("auto", triage_data)

        assert out["caseTypeID"]     == "ITSM-Work-AutoClaim"
        assert out["schema_version"] == "1.0"

        c = out["content"]
        # Mappings
        assert c["VehicleVIN"]                              == "1HGCM82633A004352"
        assert c["DriverID"]                                == "DRV-9001"
        assert c["IncidentDate"]                            == "2026-04-22"
        assert c["Content"]["PolicyNumber"]                 == "POL-12345"
        assert c["Content"]["D_FinancialDetails"]["TotalAmount"] == 4200.56
        assert c["Content"]["Severity"]                     == "S2"
        assert c["AssignedTo"]["Email"]                     == "j@example.com"
        assert c["AssignedTo"]["DisplayName"]               == "Jane Doe"
        assert c["pyAssignedToOperator"]                    == "auto-senior-adjusters"
        # Defaults
        assert c["pyStatusWork"]    == "Open"
        assert c["pyOrigin"]        == "arc-email-triage"
        assert c["pyOperatorID"]    == "arc-bot"
        assert c["Channel"]         == "email"

    def test_missing_vin_fails_required_check(
        self, shipped_registry: PegaSchemaRegistry
    ):
        triage_data = {
            "policy_number": "POL-1",
            "incident_date": "2026-04-22",
            # vehicle_vin missing!
        }
        with pytest.raises(PegaSchemaError, match="VehicleVIN"):
            shipped_registry.map("auto", triage_data)

    def test_missing_policy_number_fails(
        self, shipped_registry: PegaSchemaRegistry
    ):
        triage_data = {
            "incident_date": "2026-04-22",
            "vehicle_vin":   "VIN1",
        }
        with pytest.raises(PegaSchemaError, match="PolicyNumber"):
            shipped_registry.map("auto", triage_data)


class TestShippedPropertyClaim:
    def test_full_payload_shape(self, shipped_registry: PegaSchemaRegistry):
        triage_data = {
            "policy_number": "POL-99887",
            "claim_amount":  85_000.0,
            "incident_date": "2026-04-26",
            "property_address": {
                "line1": "123 Main St",
                "line2": "Apt 4B",
                "city":  "Brooklyn",
                "zip":   "11201",
            },
            "damage_type": "fire",
            "claimant":    {"email": "b@example.com", "name": "Bob Smith"},
            "routing":     {"team": "property-major-loss"},
            "triage":      {"severity": "S1"},
        }
        out = shipped_registry.map("property", triage_data)

        assert out["caseTypeID"] == "ITSM-Work-PropertyClaim"
        c = out["content"]
        prop = c["Content"]["PropertyDetails"]
        assert prop["AddressLine1"] == "123 Main St"
        assert prop["AddressLine2"] == "Apt 4B"
        assert prop["City"]         == "Brooklyn"
        assert prop["PostalCode"]   == "11201"
        assert c["Content"]["DamageType"] == "fire"

    def test_missing_postal_code_fails(
        self, shipped_registry: PegaSchemaRegistry
    ):
        triage_data = {
            "policy_number": "POL-1",
            "incident_date": "2026-04-26",
            "property_address": {"line1": "x"},  # zip missing
            "damage_type": "fire",
        }
        with pytest.raises(PegaSchemaError, match="PostalCode"):
            shipped_registry.map("property", triage_data)


class TestShippedRegistryBasics:
    def test_both_case_types_registered(self, shipped_registry: PegaSchemaRegistry):
        assert sorted(shipped_registry.case_types()) == ["auto", "property"]

    def test_has(self, shipped_registry: PegaSchemaRegistry):
        assert shipped_registry.has("auto")
        assert shipped_registry.has("property")
        assert not shipped_registry.has("liability")

    def test_schema_introspection(self, shipped_registry: PegaSchemaRegistry):
        s = shipped_registry.schema("auto")
        assert s["case_type"]  == "auto"
        assert s["pega_class"] == "ITSM-Work-AutoClaim"
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
            shipped_registry.map("liability", {})
        # The error message lists what *is* registered to help the caller.
        msg = str(exc.value)
        assert "auto" in msg
        assert "property" in msg
        assert "liability" in msg

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

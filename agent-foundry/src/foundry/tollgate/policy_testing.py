"""Policy testing framework for Tollgate.

Enables declarative scenario-based testing of Tollgate policies to prevent
regressions in CI. Test scenarios are defined in YAML and run against a
policy evaluator.

Usage:

    # From Python:
    from tollgate.policy_testing import PolicyTestRunner

    runner = PolicyTestRunner("policy.yaml", "test_scenarios.yaml")
    results = runner.run()
    assert results.all_passed

    # From CLI:
    tollgate test-policy policy.yaml --scenarios test_scenarios.yaml

Scenario file format:

    scenarios:
      - name: "Allow read operations"
        description: "Read effects should be allowed for trusted agents"
        agent:
          agent_id: "agent-1"
          version: "1.0"
          owner: "team-a"
        intent:
          action: "fetch_data"
          reason: "Customer request"
        tool_request:
          tool: "api:fetch"
          action: "get"
          resource_type: "url"
          effect: "read"
          params: {}
          manifest_version: "1.0.0"
        expected:
          decision: "ALLOW"       # Required: ALLOW, ASK, or DENY
          reason_contains: "Rule"  # Optional: substring match on reason
          policy_id: "allow_read"  # Optional: exact match on policy_id
"""

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .policy import YamlPolicyEvaluator
from .types import AgentContext, DecisionType, Effect, Intent, ToolRequest


@dataclass
class ScenarioResult:
    """Result of a single test scenario."""

    name: str
    passed: bool
    expected_decision: str
    actual_decision: str
    expected_reason_contains: str | None = None
    actual_reason: str | None = None
    expected_policy_id: str | None = None
    actual_policy_id: str | None = None
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        msg = f"  [{status}] {self.name}"
        if not self.passed:
            for err in self.errors:
                msg += f"\n         {err}"
        return msg


@dataclass
class PolicyTestRunResult:
    """Aggregate result of a test run."""

    scenario_results: list[ScenarioResult]
    total: int = 0
    passed: int = 0
    failed: int = 0
    duration_ms: float = 0.0

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        lines = [
            "",
            "=" * 60,
            f"  Policy Test Results: {self.passed}/{self.total} passed",
            "=" * 60,
        ]
        for result in self.scenario_results:
            lines.append(str(result))
        lines.append("-" * 60)
        status = "ALL PASSED" if self.all_passed else f"{self.failed} FAILED"
        lines.append(f"  {status} ({self.duration_ms:.1f}ms)")
        lines.append("")
        return "\n".join(lines)


class PolicyTestRunner:
    """Run declarative policy test scenarios.

    Args:
        policy_path: Path to the policy YAML file.
        scenarios_path: Path to the test scenarios YAML file.
        policy_evaluator: Optional pre-configured evaluator (overrides policy_path).
    """

    def __init__(
        self,
        policy_path: str | Path | None = None,
        scenarios_path: str | Path | None = None,
        *,
        policy_evaluator: Any | None = None,
        scenarios: list[dict[str, Any]] | None = None,
    ):
        # Load policy
        if policy_evaluator is not None:
            self._evaluator = policy_evaluator
        elif policy_path is not None:
            self._evaluator = YamlPolicyEvaluator(policy_path)
        else:
            raise ValueError("Either policy_path or policy_evaluator must be provided")

        # Load scenarios
        if scenarios is not None:
            self._scenarios = scenarios
        elif scenarios_path is not None:
            self._scenarios = self._load_scenarios(scenarios_path)
        else:
            raise ValueError("Either scenarios_path or scenarios must be provided")

        self._validate_scenarios()

    @staticmethod
    def _load_scenarios(path: str | Path) -> list[dict[str, Any]]:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Scenarios file not found: {path}")

        with path.open("r") as f:
            data = yaml.safe_load(f)

        if not data or "scenarios" not in data:
            raise ValueError(f"Scenarios file must contain a 'scenarios' key: {path}")

        return data["scenarios"]

    def _validate_scenarios(self):
        """Validate scenario structure before running."""
        for i, scenario in enumerate(self._scenarios):
            name = scenario.get("name", f"Scenario {i}")
            if "expected" not in scenario:
                raise ValueError(f"Scenario '{name}' is missing 'expected' key")
            if "decision" not in scenario["expected"]:
                raise ValueError(
                    f"Scenario '{name}' expected section must include 'decision'"
                )
            try:
                DecisionType(scenario["expected"]["decision"])
            except ValueError:
                raise ValueError(
                    f"Scenario '{name}' has invalid expected decision: "
                    f"'{scenario['expected']['decision']}'"
                ) from None

    def run(self) -> PolicyTestRunResult:
        """Run all test scenarios and return results."""
        start = time.monotonic()
        results: list[ScenarioResult] = []

        for scenario in self._scenarios:
            result = self._run_scenario(scenario)
            results.append(result)

        total_ms = (time.monotonic() - start) * 1000
        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed

        return PolicyTestRunResult(
            scenario_results=results,
            total=len(results),
            passed=passed,
            failed=failed,
            duration_ms=total_ms,
        )

    def _run_scenario(self, scenario: dict[str, Any]) -> ScenarioResult:
        """Run a single test scenario."""
        name = scenario.get("name", "Unnamed scenario")
        expected = scenario["expected"]
        expected_decision = expected["decision"]
        expected_reason_contains = expected.get("reason_contains")
        expected_policy_id = expected.get("policy_id")

        start = time.monotonic()
        errors: list[str] = []

        try:
            agent_ctx = self._build_agent_context(scenario.get("agent", {}))
            intent = self._build_intent(scenario.get("intent", {}))
            tool_request = self._build_tool_request(scenario.get("tool_request", {}))

            decision = self._evaluator.evaluate(agent_ctx, intent, tool_request)

            actual_decision = decision.decision.value
            actual_reason = decision.reason
            actual_policy_id = decision.policy_id

            # Check decision
            if actual_decision != expected_decision:
                errors.append(
                    f"Decision: expected '{expected_decision}', got '{actual_decision}'"
                )

            # Check reason (substring match)
            if expected_reason_contains and expected_reason_contains not in (
                actual_reason or ""
            ):
                errors.append(
                    f"Reason: expected to contain '{expected_reason_contains}', "
                    f"got '{actual_reason}'"
                )

            # Check policy_id
            if expected_policy_id and actual_policy_id != expected_policy_id:
                errors.append(
                    f"Policy ID: expected '{expected_policy_id}', "
                    f"got '{actual_policy_id}'"
                )

        except Exception as e:
            actual_decision = "ERROR"
            actual_reason = str(e)
            actual_policy_id = None
            errors.append(f"Exception: {e}")

        duration_ms = (time.monotonic() - start) * 1000

        return ScenarioResult(
            name=name,
            passed=len(errors) == 0,
            expected_decision=expected_decision,
            actual_decision=actual_decision,
            expected_reason_contains=expected_reason_contains,
            actual_reason=actual_reason,
            expected_policy_id=expected_policy_id,
            actual_policy_id=actual_policy_id,
            errors=errors,
            duration_ms=duration_ms,
        )

    @staticmethod
    def _build_agent_context(data: dict[str, Any]) -> AgentContext:
        delegated_by = data.get("delegated_by")
        delegated_by = tuple(delegated_by) if delegated_by is not None else ()
        return AgentContext(
            agent_id=data.get("agent_id", "test-agent"),
            version=data.get("version", "1.0"),
            owner=data.get("owner", "test-owner"),
            metadata=data.get("metadata", {}),
            delegated_by=delegated_by,
        )

    @staticmethod
    def _build_intent(data: dict[str, Any]) -> Intent:
        return Intent(
            action=data.get("action", "test_action"),
            reason=data.get("reason", "test reason"),
            confidence=data.get("confidence"),
            metadata=data.get("metadata", {}),
        )

    @staticmethod
    def _build_tool_request(data: dict[str, Any]) -> ToolRequest:
        effect_str = data.get("effect", "unknown")
        try:
            effect = Effect(effect_str)
        except ValueError:
            effect = Effect.UNKNOWN

        return ToolRequest(
            tool=data.get("tool", "unknown"),
            action=data.get("action", "unknown"),
            resource_type=data.get("resource_type", "unknown"),
            effect=effect,
            params=data.get("params", {}),
            metadata=data.get("metadata", {}),
            manifest_version=data.get("manifest_version"),
        )


def cli_main(args: list[str] | None = None) -> int:
    """CLI entry point for ``tollgate test-policy``.

    Usage:
        tollgate test-policy policy.yaml --scenarios test_scenarios.yaml
        tollgate test-policy policy.yaml -s test_scenarios.yaml --strict

    Returns exit code 0 on success, 1 on failure.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="tollgate test-policy",
        description="Run declarative policy test scenarios against a Tollgate policy.",
    )
    parser.add_argument(
        "policy_path",
        help="Path to the policy YAML file",
    )
    parser.add_argument(
        "--scenarios",
        "-s",
        required=True,
        help="Path to the test scenarios YAML file",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 on any failure (default behavior)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Only show failures and summary",
    )

    parsed = parser.parse_args(args)

    try:
        runner = PolicyTestRunner(parsed.policy_path, parsed.scenarios)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    results = runner.run()

    if parsed.quiet:
        # Only show failures
        for r in results.scenario_results:
            if not r.passed:
                print(str(r))
        print(f"\n{results.passed}/{results.total} passed, {results.failed} failed")
    else:
        print(results.summary())

    return 0 if results.all_passed else 1

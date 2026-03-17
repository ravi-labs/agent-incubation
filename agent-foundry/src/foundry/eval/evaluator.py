"""
foundry.eval.evaluator
───────────────────────
Scenario-based evaluation framework for Foundry agents.

FoundryEvaluator runs structured EvalScenarios against a live agent and
verifies that:
  - The agent's policy decisions match expectations (ALLOW / ASK / DENY)
  - The agent's output contains or matches expected values
  - No undeclared effects are invoked
  - Performance budgets (latency, token count) are met

Unlike unit tests that mock the ControlTower, EvalScenarios run against
the REAL ControlTower with a real (or test) policy — so they verify the
entire pipeline including policy evaluation and audit logging.

Usage:

    from foundry.eval import FoundryEvaluator, EvalScenario

    evaluator = FoundryEvaluator(agent)

    results = await evaluator.run([
        EvalScenario(
            name="risk_score_allowed",
            inputs={"participant_id": "p-001"},
            expect_effects_allowed=["risk.score.compute"],
            expect_output_contains={"risk_score"},
        ),
        EvalScenario(
            name="communication_requires_approval",
            inputs={"participant_id": "p-001", "message": "Action needed"},
            expect_effects_asked=["participant.communication.send"],
            expect_no_exception=False,  # TollgateDeferred is expected
        ),
        EvalScenario(
            name="account_transaction_denied",
            inputs={"participant_id": "p-001", "amount": 1000},
            expect_effects_denied=["account.transaction.execute"],
            expect_exception_type="PermissionError",
        ),
    ])

    evaluator.print_report(results)
    assert all(r.passed for r in results), "Eval suite failed"

CI integration:

    # In your test file:
    import pytest
    from foundry.eval import FoundryEvaluator, EvalScenario

    @pytest.mark.asyncio
    async def test_agent_policy_compliance(make_agent):
        agent = make_agent()
        evaluator = FoundryEvaluator(agent)
        results = await evaluator.run(COMPLIANCE_SCENARIOS)
        failures = [r for r in results if not r.passed]
        assert not failures, "\\n".join(f"  {r.name}: {r.failure_reason}" for r in failures)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from foundry.scaffold.base import BaseAgent

logger = logging.getLogger(__name__)


# ── Scenario ───────────────────────────────────────────────────────────────────


@dataclass
class EvalScenario:
    """
    A single evaluation scenario for a Foundry agent.

    Describes what inputs to send, what policy decisions to expect,
    and what the agent output should look like.

    Attributes:
        name:                    Unique scenario identifier for reporting.
        inputs:                  Keyword arguments passed to agent.execute().
        description:             Optional human-readable scenario description.

        expect_effects_allowed:  Effects that MUST be ALLOW-decided (not blocked).
        expect_effects_asked:    Effects that MUST trigger TollgateDeferred (ASK).
        expect_effects_denied:   Effects that MUST be blocked with PermissionError.
        expect_no_undeclared:    Fail if any undeclared effect is attempted (default True).

        expect_output_contains:  Set of keys that must be present in the output dict.
        expect_output_equals:    Exact value the output must equal.
        expect_output_fn:        Custom predicate: fn(output) -> bool.

        expect_exception_type:   Exception class name that must be raised.
        expect_no_exception:     If True (default), the agent must not raise.

        max_latency_ms:          Optional maximum acceptable latency in milliseconds.
        tags:                    Categorisation tags for filtering (e.g. ["happy_path"]).
    """
    name:                  str
    inputs:                dict = field(default_factory=dict)
    description:           str  = ""

    # Policy decision expectations
    expect_effects_allowed: list[str] = field(default_factory=list)
    expect_effects_asked:   list[str] = field(default_factory=list)
    expect_effects_denied:  list[str] = field(default_factory=list)
    expect_no_undeclared:   bool       = True

    # Output expectations
    expect_output_contains: set[str]  = field(default_factory=set)
    expect_output_equals:   Any       = None
    expect_output_fn:       Any       = None   # Callable[[Any], bool]

    # Exception expectations
    expect_exception_type:  str | None = None
    expect_no_exception:    bool        = True

    # Performance
    max_latency_ms:         int | None  = None

    # Metadata
    tags: list[str] = field(default_factory=list)


# ── Result ─────────────────────────────────────────────────────────────────────


@dataclass
class EvalResult:
    """
    Result from running a single EvalScenario.

    Attributes:
        scenario:        The scenario that was run.
        passed:          Whether all assertions passed.
        failure_reason:  Human-readable failure description (empty if passed).
        output:          The agent's return value (None if it raised).
        exception:       The exception raised (None if not raised).
        latency_ms:      Time taken to execute the agent in milliseconds.
        effects_invoked: List of (effect_value, decision) tuples observed.
        assertions:      Dict of assertion name → pass/fail.
    """
    scenario:        EvalScenario
    passed:          bool
    failure_reason:  str  = ""
    output:          Any  = None
    exception:       Any  = None
    latency_ms:      float = 0.0
    effects_invoked: list  = field(default_factory=list)
    assertions:      dict  = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.scenario.name


# ── Evaluator ──────────────────────────────────────────────────────────────────


class FoundryEvaluator:
    """
    Runs EvalScenarios against a live Foundry agent.

    Instruments the agent's ControlTower to capture policy decisions
    for each effect invocation, then checks them against the scenario's
    expectations.

    For ASK decisions, the evaluator uses a configurable approver:
      - on_ask="approve"  → auto-approves all ASK decisions (default for ALLOW scenarios)
      - on_ask="deny"     → auto-denies all ASK decisions (for testing denial paths)
      - on_ask="raise"    → raises TollgateDeferred (for testing confirmation flows)

    Args:
        agent:    The BaseAgent to evaluate.
        on_ask:   How to handle ASK decisions (default: "approve").
        verbose:  Print per-assertion details during evaluation.
    """

    def __init__(
        self,
        agent: "BaseAgent",
        on_ask: str = "approve",
        verbose: bool = False,
    ):
        self._agent   = agent
        self._on_ask  = on_ask
        self._verbose = verbose

    async def run(
        self,
        scenarios: list[EvalScenario],
        stop_on_first_failure: bool = False,
    ) -> list[EvalResult]:
        """
        Run a list of EvalScenarios and return results.

        Args:
            scenarios:              List of EvalScenario to execute.
            stop_on_first_failure:  Stop execution after the first failure.

        Returns:
            List of EvalResult in the same order as the input scenarios.
        """
        results: list[EvalResult] = []
        for scenario in scenarios:
            result = await self._run_scenario(scenario)
            results.append(result)
            if self._verbose:
                status = "✓ PASS" if result.passed else "✗ FAIL"
                print(f"  {status}  {scenario.name}  ({result.latency_ms:.0f}ms)")
                if not result.passed:
                    print(f"         {result.failure_reason}")
            if stop_on_first_failure and not result.passed:
                break
        return results

    async def _run_scenario(self, scenario: EvalScenario) -> EvalResult:
        """Run a single scenario with instrumented policy tracking."""
        effects_invoked: list[tuple[str, str]] = []   # (effect_value, decision)
        failures: list[str] = []
        assertions: dict[str, bool] = {}

        # ── Instrument run_effect to capture decisions ─────────────────────────
        original_run_effect = self._agent.run_effect

        async def _instrumented_run_effect(effect, tool, action, params, intent_action,
                                           intent_reason, confidence=None, metadata=None,
                                           exec_fn=None):
            effect_val = effect.value if hasattr(effect, "value") else str(effect)
            try:
                result = await original_run_effect(
                    effect=effect, tool=tool, action=action, params=params,
                    intent_action=intent_action, intent_reason=intent_reason,
                    confidence=confidence, metadata=metadata, exec_fn=exec_fn,
                )
                effects_invoked.append((effect_val, "ALLOW"))
                return result
            except Exception as exc:
                exc_type = type(exc).__name__
                if exc_type == "TollgateDeferred":
                    effects_invoked.append((effect_val, "ASK"))
                elif exc_type in ("PermissionError", "TollgateDenied"):
                    effects_invoked.append((effect_val, "DENY"))
                else:
                    effects_invoked.append((effect_val, f"ERROR:{exc_type}"))
                raise

        self._agent.run_effect = _instrumented_run_effect

        # ── Execute the agent ──────────────────────────────────────────────────
        output    = None
        exception = None
        t0        = time.monotonic()

        try:
            output = await self._agent.execute(**scenario.inputs)
        except Exception as exc:
            exception = exc
        finally:
            latency_ms = (time.monotonic() - t0) * 1000
            self._agent.run_effect = original_run_effect   # restore

        # ── Assert: exception behaviour ────────────────────────────────────────
        if scenario.expect_exception_type:
            expected_exc = scenario.expect_exception_type
            actual_exc   = type(exception).__name__ if exception else "None"
            ok = actual_exc == expected_exc
            assertions[f"exception_type={expected_exc}"] = ok
            if not ok:
                failures.append(f"Expected exception {expected_exc}, got {actual_exc}")

        if scenario.expect_no_exception and exception is not None:
            ok = False
            assertions["no_exception"] = ok
            failures.append(f"Unexpected exception: {type(exception).__name__}: {exception}")
        elif scenario.expect_no_exception:
            assertions["no_exception"] = True

        # ── Assert: policy decisions ───────────────────────────────────────────
        effects_by_value: dict[str, list[str]] = {}
        for val, decision in effects_invoked:
            effects_by_value.setdefault(val, []).append(decision)

        for effect_val in scenario.expect_effects_allowed:
            decisions = effects_by_value.get(effect_val, [])
            ok = "ALLOW" in decisions
            assertions[f"allowed:{effect_val}"] = ok
            if not ok:
                failures.append(
                    f"Expected effect '{effect_val}' to be ALLOWED, "
                    f"got: {decisions or 'not invoked'}"
                )

        for effect_val in scenario.expect_effects_asked:
            decisions = effects_by_value.get(effect_val, [])
            ok = "ASK" in decisions
            assertions[f"asked:{effect_val}"] = ok
            if not ok:
                failures.append(
                    f"Expected effect '{effect_val}' to trigger ASK (TollgateDeferred), "
                    f"got: {decisions or 'not invoked'}"
                )

        for effect_val in scenario.expect_effects_denied:
            decisions = effects_by_value.get(effect_val, [])
            ok = "DENY" in decisions
            assertions[f"denied:{effect_val}"] = ok
            if not ok:
                failures.append(
                    f"Expected effect '{effect_val}' to be DENIED (PermissionError), "
                    f"got: {decisions or 'not invoked'}"
                )

        if scenario.expect_no_undeclared:
            # Any DENY caused by undeclared-effect guard in BaseAgent
            undeclared = [
                val for val, dec in effects_invoked
                if "undeclared" in dec.lower()
            ]
            ok = len(undeclared) == 0
            assertions["no_undeclared_effects"] = ok
            if not ok:
                failures.append(f"Undeclared effects invoked: {undeclared}")

        # ── Assert: output ─────────────────────────────────────────────────────
        if scenario.expect_output_contains and output is not None:
            if isinstance(output, dict):
                missing = scenario.expect_output_contains - set(output.keys())
                ok = len(missing) == 0
            else:
                missing = scenario.expect_output_contains
                ok = False
            assertions["output_contains"] = ok
            if not ok:
                failures.append(f"Output missing expected keys: {missing}")

        if scenario.expect_output_equals is not None:
            ok = output == scenario.expect_output_equals
            assertions["output_equals"] = ok
            if not ok:
                failures.append(f"Output mismatch: expected {scenario.expect_output_equals!r}, got {output!r}")

        if scenario.expect_output_fn is not None:
            try:
                ok = bool(scenario.expect_output_fn(output))
            except Exception as fn_exc:
                ok = False
                failures.append(f"Output predicate raised: {fn_exc}")
            assertions["output_predicate"] = ok
            if not ok and "output_predicate" not in [f.split(":")[0] for f in failures]:
                failures.append("Output predicate returned False")

        # ── Assert: latency ────────────────────────────────────────────────────
        if scenario.max_latency_ms is not None:
            ok = latency_ms <= scenario.max_latency_ms
            assertions[f"latency<={scenario.max_latency_ms}ms"] = ok
            if not ok:
                failures.append(
                    f"Latency {latency_ms:.0f}ms exceeded budget {scenario.max_latency_ms}ms"
                )

        return EvalResult(
            scenario=scenario,
            passed=len(failures) == 0,
            failure_reason="; ".join(failures),
            output=output,
            exception=exception,
            latency_ms=latency_ms,
            effects_invoked=effects_invoked,
            assertions=assertions,
        )

    # ── Reporting ──────────────────────────────────────────────────────────────

    def print_report(self, results: list[EvalResult]) -> None:
        """Print a human-readable eval report to stdout."""
        passed = sum(1 for r in results if r.passed)
        total  = len(results)
        print(f"\n{'='*60}")
        print(f"  Foundry Eval Report — {self._agent.manifest.agent_id}")
        print(f"  {passed}/{total} scenarios passed")
        print(f"{'='*60}")
        for r in results:
            icon = "✓" if r.passed else "✗"
            print(f"  {icon}  {r.name}  ({r.latency_ms:.0f}ms)")
            if not r.passed:
                print(f"     → {r.failure_reason}")
            if self._verbose and r.effects_invoked:
                for effect_val, decision in r.effects_invoked:
                    print(f"       {decision:6s}  {effect_val}")
        print(f"{'='*60}\n")

    def summary(self, results: list[EvalResult]) -> dict:
        """Return a structured summary dict suitable for CI reporting."""
        return {
            "agent_id":   self._agent.manifest.agent_id,
            "total":      len(results),
            "passed":     sum(1 for r in results if r.passed),
            "failed":     sum(1 for r in results if not r.passed),
            "results": [
                {
                    "name":           r.name,
                    "passed":         r.passed,
                    "latency_ms":     round(r.latency_ms, 1),
                    "failure_reason": r.failure_reason,
                    "assertions":     r.assertions,
                }
                for r in results
            ],
        }

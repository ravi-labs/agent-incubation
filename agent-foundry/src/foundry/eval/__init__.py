"""
foundry.eval
────────────
Scenario-based evaluation framework for agent-foundry agents.

Provides:
  - EvalScenario   — describe an agent test case (inputs, expected policy decisions, expected outputs)
  - EvalResult     — structured result from running a scenario
  - FoundryEvaluator — runs EvalScenarios against a live agent and reports pass/fail

Install:
    pip install "agent-foundry"   # no extra deps needed

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
            inputs={"participant_id": "p-001", "message": "You are at risk"},
            expect_effects_asked=["participant.communication.send"],
        ),
    ])

    evaluator.print_report(results)
"""
from foundry.eval.evaluator import EvalScenario, EvalResult, FoundryEvaluator

__all__ = ["EvalScenario", "EvalResult", "FoundryEvaluator"]

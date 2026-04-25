"""
arc.eval — scenario-based evaluation framework for arc agents.

Provides:
  - EvalScenario       describe a test case (inputs, expected policy decisions,
                       expected outputs)
  - EvalResult         structured result from running a scenario
  - FoundryEvaluator   runs EvalScenarios against a live agent and reports pass/fail
"""

from .evaluator import EvalResult, EvalScenario, FoundryEvaluator

__all__ = ["EvalScenario", "EvalResult", "FoundryEvaluator"]

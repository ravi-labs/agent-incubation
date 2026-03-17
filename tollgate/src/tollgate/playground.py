"""Policy Playground - Interactive CLI for testing Tollgate policies.

Provides a REPL environment for policy authors to interactively test
their policies with various scenarios.

Usage:

    # CLI
    tollgate playground policy.yaml

    # Python
    from tollgate.playground import PolicyPlayground

    playground = PolicyPlayground("policy.yaml")
    playground.run()  # Starts interactive REPL

Features:
    - Interactive scenario building
    - Decision explanation with DecisionExplainer
    - Load/save scenarios
    - Batch testing
    - Watch mode for policy file changes
"""

import cmd
import json
import sys
from pathlib import Path

import yaml

from .explainer import DecisionExplainer
from .policy import YamlPolicyEvaluator
from .types import AgentContext, DecisionType, Effect, Intent, ToolRequest


class PolicyPlayground(cmd.Cmd):
    """Interactive REPL for testing Tollgate policies."""

    intro = """
╔══════════════════════════════════════════════════════════════════════╗
║                     TOLLGATE POLICY PLAYGROUND                       ║
╠══════════════════════════════════════════════════════════════════════╣
║  Commands:                                                           ║
║    set <field> <value>  - Set a scenario field                       ║
║    show                 - Show current scenario                      ║
║    eval                 - Evaluate current scenario                  ║
║    explain              - Evaluate with detailed explanation         ║
║    reset                - Reset scenario to defaults                 ║
║    load <file>          - Load scenario from YAML/JSON               ║
║    save <file>          - Save current scenario                      ║
║    reload               - Reload policy file                         ║
║    rules                - Show all policy rules                      ║
║    help [cmd]           - Show help                                  ║
║    quit                 - Exit playground                            ║
╚══════════════════════════════════════════════════════════════════════╝
"""
    prompt = "tollgate> "

    def __init__(
        self,
        policy_path: str | Path,
        *,
        auto_reload: bool = False,
    ):
        super().__init__()
        self.policy_path = Path(policy_path)
        self.auto_reload = auto_reload
        self._policy_mtime: float = 0

        self._load_policy()
        self._reset_scenario()

    def _load_policy(self) -> None:
        """Load or reload the policy file."""
        self._evaluator = YamlPolicyEvaluator(self.policy_path)
        self._explainer = DecisionExplainer(self._evaluator)
        self._policy_mtime = self.policy_path.stat().st_mtime
        print(f"Loaded policy: {self.policy_path} (version: {self._evaluator.version})")

    def _check_reload(self) -> None:
        """Check if policy file has changed and reload if needed."""
        if not self.auto_reload:
            return
        try:
            current_mtime = self.policy_path.stat().st_mtime
            if current_mtime > self._policy_mtime:
                print("\n[Policy file changed, reloading...]")
                self._load_policy()
        except Exception as e:
            print(f"[Warning: Could not check policy file: {e}]")

    def _reset_scenario(self) -> None:
        """Reset the current scenario to defaults."""
        self._scenario = {
            "agent": {
                "agent_id": "test-agent",
                "version": "1.0",
                "owner": "test-owner",
                "metadata": {},
                "delegated_by": [],
            },
            "intent": {
                "action": "test",
                "reason": "testing",
                "confidence": None,
                "metadata": {},
            },
            "tool_request": {
                "tool": "test:tool",
                "action": "test",
                "resource_type": "test",
                "effect": "read",
                "params": {},
                "metadata": {},
                "manifest_version": "1.0.0",
            },
        }

    def _build_objects(
        self,
    ) -> tuple[AgentContext, Intent, ToolRequest]:
        """Build typed objects from current scenario."""
        agent_data = self._scenario["agent"]
        delegated_by = agent_data.get("delegated_by", [])
        if isinstance(delegated_by, list):
            delegated_by = tuple(delegated_by)

        agent_ctx = AgentContext(
            agent_id=agent_data["agent_id"],
            version=agent_data["version"],
            owner=agent_data["owner"],
            metadata=agent_data.get("metadata", {}),
            delegated_by=delegated_by,
        )

        intent_data = self._scenario["intent"]
        intent = Intent(
            action=intent_data["action"],
            reason=intent_data["reason"],
            confidence=intent_data.get("confidence"),
            metadata=intent_data.get("metadata", {}),
        )

        req_data = self._scenario["tool_request"]
        try:
            effect = Effect(req_data["effect"])
        except ValueError:
            effect = Effect.UNKNOWN

        tool_request = ToolRequest(
            tool=req_data["tool"],
            action=req_data["action"],
            resource_type=req_data["resource_type"],
            effect=effect,
            params=req_data.get("params", {}),
            metadata=req_data.get("metadata", {}),
            manifest_version=req_data.get("manifest_version"),
        )

        return agent_ctx, intent, tool_request

    def precmd(self, line: str) -> str:
        """Hook called before each command."""
        self._check_reload()
        return line

    def do_set(self, arg: str) -> None:
        """Set a scenario field: set <path> <value>

        Examples:
            set agent.agent_id my-agent
            set tool_request.effect write
            set tool_request.metadata.amount 100
            set agent.delegated_by ["agent-a", "agent-b"]
        """
        parts = arg.split(None, 1)
        if len(parts) < 2:
            print("Usage: set <path> <value>")
            print("Example: set agent.agent_id my-agent")
            return

        path, value_str = parts

        # Parse value
        try:
            # Try JSON first
            value = json.loads(value_str)
        except json.JSONDecodeError:
            # Fall back to string
            value = value_str

        # Navigate path
        keys = path.split(".")
        target = self._scenario

        for key in keys[:-1]:
            if key not in target:
                target[key] = {}
            target = target[key]

        final_key = keys[-1]
        target[final_key] = value
        print(f"Set {path} = {value!r}")

    def do_show(self, arg: str) -> None:
        """Show the current scenario."""
        print("\nCurrent Scenario:")
        print("-" * 40)
        print(yaml.dump(self._scenario, default_flow_style=False, sort_keys=False))

    def do_eval(self, arg: str) -> None:
        """Evaluate the current scenario against the policy."""
        try:
            agent_ctx, intent, tool_request = self._build_objects()
            decision = self._evaluator.evaluate(agent_ctx, intent, tool_request)

            # Color output based on decision
            decision_str = decision.decision.value
            if decision.decision == DecisionType.ALLOW:
                color = "\033[92m"  # Green
            elif decision.decision == DecisionType.DENY:
                color = "\033[91m"  # Red
            else:
                color = "\033[93m"  # Yellow

            reset = "\033[0m"

            print(f"\nDecision: {color}{decision_str}{reset}")
            print(f"Reason: {decision.reason}")
            if decision.policy_id:
                print(f"Policy ID: {decision.policy_id}")
            print()

        except Exception as e:
            print(f"Error: {e}")

    def do_explain(self, arg: str) -> None:
        """Evaluate with detailed explanation."""
        try:
            agent_ctx, intent, tool_request = self._build_objects()
            result = self._explainer.explain(agent_ctx, intent, tool_request)
            result.print()
        except Exception as e:
            print(f"Error: {e}")

    def do_reset(self, arg: str) -> None:
        """Reset scenario to defaults."""
        self._reset_scenario()
        print("Scenario reset to defaults.")

    def do_load(self, arg: str) -> None:
        """Load a scenario from a YAML or JSON file."""
        if not arg:
            print("Usage: load <file>")
            return

        path = Path(arg)
        if not path.exists():
            print(f"File not found: {path}")
            return

        try:
            with path.open() as f:
                if path.suffix == ".json":
                    data = json.load(f)
                else:
                    data = yaml.safe_load(f)

            # Merge with defaults
            if "agent" in data:
                self._scenario["agent"].update(data["agent"])
            if "intent" in data:
                self._scenario["intent"].update(data["intent"])
            if "tool_request" in data:
                self._scenario["tool_request"].update(data["tool_request"])

            print(f"Loaded scenario from {path}")
            self.do_show("")

        except Exception as e:
            print(f"Error loading file: {e}")

    def do_save(self, arg: str) -> None:
        """Save the current scenario to a file."""
        if not arg:
            print("Usage: save <file>")
            return

        path = Path(arg)
        try:
            with path.open("w") as f:
                if path.suffix == ".json":
                    json.dump(self._scenario, f, indent=2)
                else:
                    yaml.dump(self._scenario, f, default_flow_style=False)

            print(f"Saved scenario to {path}")

        except Exception as e:
            print(f"Error saving file: {e}")

    def do_reload(self, arg: str) -> None:
        """Reload the policy file."""
        try:
            self._load_policy()
        except Exception as e:
            print(f"Error reloading policy: {e}")

    def do_rules(self, arg: str) -> None:
        """Show all policy rules."""
        print(f"\nPolicy: {self.policy_path}")
        print(f"Version: {self._evaluator.version}")
        print(f"Rules: {len(self._evaluator.rules)}")
        print("-" * 60)

        for i, rule in enumerate(self._evaluator.rules):
            rule_id = rule.get("id", f"Rule #{i}")
            decision = rule.get("decision", "?")

            # Build conditions summary
            conditions = []
            if "tool" in rule:
                conditions.append(f"tool={rule['tool']}")
            if "action" in rule:
                conditions.append(f"action={rule['action']}")
            if "effect" in rule:
                conditions.append(f"effect={rule['effect']}")
            if "agent" in rule:
                agent_conds = ", ".join(
                    f"{k}={v}" for k, v in rule["agent"].items()
                )
                conditions.append(f"agent({agent_conds})")
            if "when" in rule:
                conditions.append(f"when({len(rule['when'])} conditions)")

            cond_str = " AND ".join(conditions) if conditions else "(catch-all)"
            print(f"  [{i}] {rule_id}: {decision}")
            print(f"      {cond_str}")
            if "reason" in rule:
                print(f"      -> {rule['reason']}")
            print()

    def do_batch(self, arg: str) -> None:
        """Run batch tests from a scenarios file.

        Usage: batch <scenarios.yaml>

        The file should have the same format as policy test scenarios.
        """
        if not arg:
            print("Usage: batch <scenarios.yaml>")
            return

        path = Path(arg)
        if not path.exists():
            print(f"File not found: {path}")
            return

        try:
            with path.open() as f:
                data = yaml.safe_load(f)

            scenarios = data.get("scenarios", [])
            if not scenarios:
                print("No scenarios found in file.")
                return

            print(f"\nRunning {len(scenarios)} scenarios...")
            print("-" * 60)

            passed = 0
            failed = 0

            for scenario in scenarios:
                name = scenario.get("name", "Unnamed")
                expected = scenario.get("expected", {})
                expected_decision = expected.get("decision")

                # Build objects from scenario
                agent_data = scenario.get("agent", {})
                intent_data = scenario.get("intent", {})
                req_data = scenario.get("tool_request", {})

                delegated_by = agent_data.get("delegated_by", [])
                if isinstance(delegated_by, list):
                    delegated_by = tuple(delegated_by)

                agent_ctx = AgentContext(
                    agent_id=agent_data.get("agent_id", "test"),
                    version=agent_data.get("version", "1.0"),
                    owner=agent_data.get("owner", "test"),
                    metadata=agent_data.get("metadata", {}),
                    delegated_by=delegated_by,
                )

                intent = Intent(
                    action=intent_data.get("action", "test"),
                    reason=intent_data.get("reason", "test"),
                    confidence=intent_data.get("confidence"),
                    metadata=intent_data.get("metadata", {}),
                )

                try:
                    effect = Effect(req_data.get("effect", "unknown"))
                except ValueError:
                    effect = Effect.UNKNOWN

                tool_request = ToolRequest(
                    tool=req_data.get("tool", "test"),
                    action=req_data.get("action", "test"),
                    resource_type=req_data.get("resource_type", "test"),
                    effect=effect,
                    params=req_data.get("params", {}),
                    metadata=req_data.get("metadata", {}),
                    manifest_version=req_data.get("manifest_version"),
                )

                # Evaluate
                decision = self._evaluator.evaluate(agent_ctx, intent, tool_request)
                actual = decision.decision.value

                if expected_decision and actual == expected_decision:
                    print(f"  \033[92m[PASS]\033[0m {name}")
                    passed += 1
                elif expected_decision:
                    print(f"  \033[91m[FAIL]\033[0m {name}")
                    print(f"         Expected: {expected_decision}, Got: {actual}")
                    failed += 1
                else:
                    print(f"  \033[93m[????]\033[0m {name} -> {actual}")

            print("-" * 60)
            total = passed + failed
            if failed == 0:
                print(f"\033[92mAll {passed} tests passed!\033[0m")
            else:
                print(f"\033[91m{passed}/{total} passed, {failed} failed\033[0m")

        except Exception as e:
            print(f"Error running batch: {e}")

    def do_quit(self, arg: str) -> bool:
        """Exit the playground."""
        print("Goodbye!")
        return True

    def do_exit(self, arg: str) -> bool:
        """Exit the playground (alias for quit)."""
        return self.do_quit(arg)

    def do_EOF(self, arg: str) -> bool:
        """Handle Ctrl+D."""
        print()
        return self.do_quit(arg)

    def emptyline(self) -> None:
        """Handle empty input."""
        pass

    def default(self, line: str) -> None:
        """Handle unknown commands."""
        print(f"Unknown command: {line}")
        print("Type 'help' for available commands.")

    def run(self) -> None:
        """Start the interactive REPL."""
        try:
            self.cmdloop()
        except KeyboardInterrupt:
            print("\nInterrupted. Goodbye!")


def cli_main(args: list[str] | None = None) -> int:
    """CLI entry point for ``tollgate playground``.

    Usage:
        tollgate playground policy.yaml
        tollgate playground policy.yaml --watch

    Returns exit code 0 on success, 1 on failure.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="tollgate playground",
        description="Interactive REPL for testing Tollgate policies.",
    )
    parser.add_argument(
        "policy_path",
        help="Path to the policy YAML file",
    )
    parser.add_argument(
        "--watch",
        "-w",
        action="store_true",
        help="Watch policy file for changes and auto-reload",
    )

    parsed = parser.parse_args(args)

    try:
        playground = PolicyPlayground(
            parsed.policy_path,
            auto_reload=parsed.watch,
        )
        playground.run()
        return 0
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

"""Tollgate CLI - Command line interface for Tollgate tools.

Provides unified access to all Tollgate CLI tools:
    - test-policy: Run policy test scenarios
    - playground: Interactive policy testing REPL
    - audit: View and analyze audit logs
    - policy: Policy management commands
    - grant: Grant management commands
    - health: System health check

Usage:
    tollgate test-policy policy.yaml -s scenarios.yaml
    tollgate playground policy.yaml
    tollgate audit logs audit.jsonl
    tollgate policy validate policy.yaml
    tollgate policy diff old.yaml new.yaml
    tollgate grant list --org org-123
    tollgate health
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import NoReturn


def main(args: list[str] | None = None) -> int:
    """Main CLI entry point."""
    if args is None:
        args = sys.argv[1:]

    if not args:
        print_help()
        return 0

    command = args[0]
    remaining = args[1:]

    if command in ("--help", "-h"):
        print_help()
        return 0

    if command in ("--version", "-V"):
        from . import __version__

        print(f"tollgate {__version__}")
        return 0

    # Dispatch to subcommands
    if command == "test-policy":
        from .policy_testing import cli_main

        return cli_main(remaining)

    if command == "playground":
        from .playground import cli_main

        return cli_main(remaining)

    if command == "audit":
        from .audit_viewer import cli_main

        return cli_main(remaining)

    if command == "policy":
        return policy_command(remaining)

    if command == "grant":
        return grant_command(remaining)

    if command == "health":
        return health_command(remaining)

    print(f"Unknown command: {command}")
    print("Run 'tollgate --help' for available commands.")
    return 1


def policy_command(args: list[str]) -> int:
    """Handle policy subcommands."""
    if not args or args[0] in ("--help", "-h"):
        print(
            """
Usage: tollgate policy <subcommand> [options]

Subcommands:
    validate <policy.yaml>         Validate policy syntax and structure
    diff <old.yaml> <new.yaml>     Compare two policy files

Examples:
    tollgate policy validate policy.yaml
    tollgate policy diff old_policy.yaml new_policy.yaml
"""
        )
        return 0

    subcommand = args[0]
    remaining = args[1:]

    if subcommand == "validate":
        return policy_validate(remaining)
    if subcommand == "diff":
        return policy_diff(remaining)

    print(f"Unknown policy subcommand: {subcommand}")
    return 1


def policy_validate(args: list[str]) -> int:
    """Validate a policy file."""
    parser = argparse.ArgumentParser(
        prog="tollgate policy validate",
        description="Validate policy syntax and structure",
    )
    parser.add_argument("policy_file", help="Path to the policy YAML file")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show detailed output"
    )

    parsed = parser.parse_args(args)
    policy_path = Path(parsed.policy_file)

    if not policy_path.exists():
        print(f"Error: Policy file not found: {policy_path}")
        return 1

    try:
        from .policy import YamlPolicyEvaluator

        evaluator = YamlPolicyEvaluator(policy_path)

        # Get policy metadata
        policy_data = evaluator.data  # Access internal for validation details

        print(f"✓ Policy file is valid: {policy_path}")

        if parsed.verbose:
            version = policy_data.get("version", "unversioned")
            rules = policy_data.get("rules", [])
            defaults = policy_data.get("defaults", {})

            print(f"\n  Version: {version}")
            print(f"  Rules: {len(rules)}")

            if defaults:
                print(f"  Default decision: {defaults.get('decision', 'not set')}")

            print("\n  Rule summary:")
            for i, rule in enumerate(rules, 1):
                rule_id = rule.get("id", f"rule-{i}")
                decision = rule.get("decision", "?")
                conditions = []
                if rule.get("tool"):
                    conditions.append(f"tool={rule['tool']}")
                if rule.get("effect"):
                    conditions.append(f"effect={rule['effect']}")
                if rule.get("agent_id"):
                    conditions.append(f"agent={rule['agent_id']}")

                cond_str = ", ".join(conditions) if conditions else "catch-all"
                print(f"    {i}. [{rule_id}] {decision} - {cond_str}")

        return 0

    except Exception as e:
        print(f"✗ Policy validation failed: {e}")
        return 1


def policy_diff(args: list[str]) -> int:
    """Compare two policy files."""
    parser = argparse.ArgumentParser(
        prog="tollgate policy diff",
        description="Compare two policy files",
    )
    parser.add_argument("old_policy", help="Path to the old policy YAML file")
    parser.add_argument("new_policy", help="Path to the new policy YAML file")

    parsed = parser.parse_args(args)

    old_path = Path(parsed.old_policy)
    new_path = Path(parsed.new_policy)

    if not old_path.exists():
        print(f"Error: Old policy file not found: {old_path}")
        return 1
    if not new_path.exists():
        print(f"Error: New policy file not found: {new_path}")
        return 1

    try:
        import yaml

        with old_path.open() as f:
            old_policy = yaml.safe_load(f)
        with new_path.open() as f:
            new_policy = yaml.safe_load(f)

        print(f"Comparing {old_path} → {new_path}\n")

        # Version comparison
        old_version = old_policy.get("version", "unversioned")
        new_version = new_policy.get("version", "unversioned")
        if old_version != new_version:
            print(f"Version: {old_version} → {new_version}")

        # Defaults comparison
        old_defaults = old_policy.get("defaults", {})
        new_defaults = new_policy.get("defaults", {})
        if old_defaults != new_defaults:
            print(f"Defaults changed: {old_defaults} → {new_defaults}")

        # Rules comparison
        old_rules = {r.get("id", f"rule-{i}"): r for i, r in enumerate(
            old_policy.get("rules", [])
        )}
        new_rules = {r.get("id", f"rule-{i}"): r for i, r in enumerate(
            new_policy.get("rules", [])
        )}

        old_ids = set(old_rules.keys())
        new_ids = set(new_rules.keys())

        added = new_ids - old_ids
        removed = old_ids - new_ids
        common = old_ids & new_ids

        if added:
            print(f"\n+ Added rules ({len(added)}):")
            for rule_id in sorted(added):
                rule = new_rules[rule_id]
                print(f"    + {rule_id}: {rule.get('decision', '?')}")

        if removed:
            print(f"\n- Removed rules ({len(removed)}):")
            for rule_id in sorted(removed):
                rule = old_rules[rule_id]
                print(f"    - {rule_id}: {rule.get('decision', '?')}")

        modified = []
        for rule_id in sorted(common):
            if old_rules[rule_id] != new_rules[rule_id]:
                modified.append(rule_id)

        if modified:
            print(f"\n~ Modified rules ({len(modified)}):")
            for rule_id in modified:
                old_dec = old_rules[rule_id].get("decision", "?")
                new_dec = new_rules[rule_id].get("decision", "?")
                if old_dec != new_dec:
                    print(f"    ~ {rule_id}: {old_dec} → {new_dec}")
                else:
                    print(f"    ~ {rule_id}: conditions changed")

        if not added and not removed and not modified:
            print("No differences found.")

        return 0

    except Exception as e:
        print(f"Error comparing policies: {e}")
        return 1


def grant_command(args: list[str]) -> int:
    """Handle grant subcommands."""
    if not args or args[0] in ("--help", "-h"):
        print(
            """
Usage: tollgate grant <subcommand> [options]

Subcommands:
    list    List active grants
    create  Create a new grant
    revoke  Revoke a grant

Examples:
    tollgate grant list
    tollgate grant list --org org-123 --agent agent-1
    tollgate grant create --agent agent-1 --tool "api:*" --effect read
    tollgate grant revoke <grant-id>
"""
        )
        return 0

    subcommand = args[0]
    remaining = args[1:]

    if subcommand == "list":
        return grant_list(remaining)
    if subcommand == "create":
        return grant_create(remaining)
    if subcommand == "revoke":
        return grant_revoke(remaining)

    print(f"Unknown grant subcommand: {subcommand}")
    return 1


def grant_list(args: list[str]) -> int:
    """List active grants."""
    parser = argparse.ArgumentParser(
        prog="tollgate grant list",
        description="List active grants",
    )
    parser.add_argument("--org", help="Filter by organization ID")
    parser.add_argument("--agent", help="Filter by agent ID")
    parser.add_argument(
        "--store",
        help="Grant store path (SQLite) or Redis URL",
        default="grants.db",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    parsed = parser.parse_args(args)

    async def _list():
        store = _get_store(parsed.store)

        try:
            grants = await store.list_active_grants(
                agent_id=parsed.agent,
                org_id=parsed.org,
            )
        except TypeError:
            # Fallback for stores that don't support org_id
            grants = await store.list_active_grants(agent_id=parsed.agent)

        if parsed.json:
            output = [g.to_dict() for g in grants]
            print(json.dumps(output, indent=2, default=str))
        else:
            if not grants:
                print("No active grants found.")
                return

            print(f"Active grants ({len(grants)}):\n")
            for grant in grants:
                expires = time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(grant.expires_at),
                )
                print(f"  ID: {grant.id}")
                print(f"  Agent: {grant.agent_id or '*'}")
                print(f"  Tool: {grant.tool or '*'}")
                print(f"  Effect: {grant.effect.value if grant.effect else '*'}")
                print(f"  Expires: {expires}")
                if grant.org_id:
                    print(f"  Org: {grant.org_id}")
                print()

    asyncio.run(_list())
    return 0


def grant_create(args: list[str]) -> int:
    """Create a new grant."""
    parser = argparse.ArgumentParser(
        prog="tollgate grant create",
        description="Create a new grant",
    )
    parser.add_argument("--agent", help="Agent ID (or * for all)")
    parser.add_argument("--tool", help="Tool pattern (e.g., 'api:*')")
    parser.add_argument("--action", help="Action pattern")
    parser.add_argument("--effect", choices=["read", "write", "delete", "notify"])
    parser.add_argument("--resource", help="Resource type")
    parser.add_argument("--org", help="Organization ID")
    parser.add_argument(
        "--duration",
        type=int,
        default=3600,
        help="Duration in seconds (default: 3600)",
    )
    parser.add_argument(
        "--granted-by",
        default="cli",
        help="Who granted this (default: cli)",
    )
    parser.add_argument(
        "--store",
        help="Grant store path (SQLite) or Redis URL",
        default="grants.db",
    )

    parsed = parser.parse_args(args)

    from .types import Effect, Grant

    async def _create():
        store = _get_store(parsed.store)

        effect = None
        if parsed.effect:
            effect = Effect(parsed.effect)

        grant = Grant(
            agent_id=parsed.agent if parsed.agent != "*" else None,
            tool=parsed.tool if parsed.tool != "*" else None,
            action=parsed.action if parsed.action and parsed.action != "*" else None,
            resource_type=parsed.resource,
            effect=effect,
            org_id=parsed.org,
            expires_at=time.time() + parsed.duration,
            granted_by=parsed.granted_by,
            created_at=time.time(),
        )

        grant_id = await store.create_grant(grant)
        print(f"Created grant: {grant_id}")
        print(f"  Agent: {grant.agent_id or '*'}")
        print(f"  Tool: {grant.tool or '*'}")
        print(f"  Effect: {grant.effect.value if grant.effect else '*'}")
        print(f"  Duration: {parsed.duration}s")

    asyncio.run(_create())
    return 0


def grant_revoke(args: list[str]) -> int:
    """Revoke a grant."""
    parser = argparse.ArgumentParser(
        prog="tollgate grant revoke",
        description="Revoke a grant by ID",
    )
    parser.add_argument("grant_id", help="Grant ID to revoke")
    parser.add_argument(
        "--store",
        help="Grant store path (SQLite) or Redis URL",
        default="grants.db",
    )

    parsed = parser.parse_args(args)

    async def _revoke():
        store = _get_store(parsed.store)

        success = await store.revoke_grant(parsed.grant_id)
        if success:
            print(f"Revoked grant: {parsed.grant_id}")
        else:
            print(f"Grant not found or already revoked: {parsed.grant_id}")

    asyncio.run(_revoke())
    return 0


def _get_store(store_path: str):
    """Get a grant store from path or URL."""
    if store_path.startswith("redis://"):
        try:
            from .backends.redis_store import RedisGrantStore

            return RedisGrantStore(store_path)
        except ImportError:
            print("Error: Redis support not installed. Run: pip install tollgate[redis]")  # noqa: E501
            sys.exit(1)
    else:
        from .backends.sqlite_store import SQLiteGrantStore

        return SQLiteGrantStore(store_path)


def health_command(args: list[str]) -> int:
    """Run system health checks."""
    parser = argparse.ArgumentParser(
        prog="tollgate health",
        description="Run system health checks",
    )
    parser.add_argument(
        "--store",
        help="Grant store path (SQLite) or Redis URL",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    parsed = parser.parse_args(args)

    checks = {
        "version": {"status": "ok", "value": None},
        "policy_engine": {"status": "ok", "value": None},
        "grant_store": {"status": "ok", "value": None},
        "optional_deps": {"status": "ok", "value": {}},
    }

    # Version check
    from . import __version__

    checks["version"]["value"] = __version__

    # Policy engine check
    try:
        import tempfile

        from .policy import YamlPolicyEvaluator

        # Test minimal policy with a temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("version: health-check\nrules: []\n")
            temp_path = f.name

        try:
            _evaluator = YamlPolicyEvaluator(temp_path)  # noqa: F841
            checks["policy_engine"]["value"] = "YamlPolicyEvaluator"
        finally:
            Path(temp_path).unlink(missing_ok=True)
    except Exception as e:
        checks["policy_engine"]["status"] = "error"
        checks["policy_engine"]["value"] = str(e)

    # Grant store check
    if parsed.store:
        try:
            store = _get_store(parsed.store)

            async def _check():
                grants = await store.list_active_grants()
                return len(grants)

            count = asyncio.run(_check())
            checks["grant_store"]["value"] = f"{count} active grants"
        except Exception as e:
            checks["grant_store"]["status"] = "error"
            checks["grant_store"]["value"] = str(e)
    else:
        checks["grant_store"]["status"] = "skipped"
        checks["grant_store"]["value"] = "No store specified"

    # Optional dependencies check
    optional_deps: dict = checks["optional_deps"]["value"]

    try:
        import cryptography

        optional_deps["cryptography"] = cryptography.__version__
    except ImportError:
        optional_deps["cryptography"] = "not installed"

    try:
        import importlib.util

        if importlib.util.find_spec("opentelemetry"):
            optional_deps["opentelemetry"] = "installed"
        else:
            optional_deps["opentelemetry"] = "not installed"
    except Exception:
        optional_deps["opentelemetry"] = "not installed"

    try:
        import redis

        optional_deps["redis"] = redis.__version__
    except ImportError:
        optional_deps["redis"] = "not installed"

    # Output
    if parsed.json:
        print(json.dumps(checks, indent=2))
    else:
        print("Tollgate Health Check")
        print("=" * 40)
        print()

        for check_name, result in checks.items():
            status = result["status"]
            value = result["value"]

            if status == "ok":
                icon = "✓"
            elif status == "skipped":
                icon = "○"
            else:
                icon = "✗"

            print(f"{icon} {check_name.replace('_', ' ').title()}")

            if isinstance(value, dict):
                for k, v in value.items():
                    print(f"    {k}: {v}")
            else:
                print(f"    {value}")
            print()

    # Return non-zero if any checks failed
    failed = any(c["status"] == "error" for c in checks.values())
    return 1 if failed else 0


def print_help() -> None:
    """Print CLI help message."""
    print(
        """
Tollgate - Runtime enforcement layer for AI agent tool calls

Usage: tollgate <command> [options]

Commands:
    test-policy   Run declarative policy test scenarios
    playground    Interactive REPL for testing policies
    audit         View and analyze audit logs
    policy        Policy management commands (validate, diff)
    grant         Grant management commands (list, create, revoke)
    health        System health check

Options:
    -h, --help     Show this help message
    -V, --version  Show version number

Examples:
    # Test a policy against scenarios
    tollgate test-policy policy.yaml -s test_scenarios.yaml

    # Start the interactive playground
    tollgate playground policy.yaml

    # View audit logs
    tollgate audit logs audit.jsonl --agent agent-1 --limit 50

    # Validate a policy file
    tollgate policy validate policy.yaml -v

    # Compare two policy files
    tollgate policy diff old.yaml new.yaml

    # List active grants
    tollgate grant list --org org-123

    # Create a grant
    tollgate grant create --agent agent-1 --tool "api:*" --effect read

    # Check system health
    tollgate health

For command-specific help, run:
    tollgate <command> --help
"""
    )


def cli_entry() -> NoReturn:
    """Entry point for console script."""
    sys.exit(main())


if __name__ == "__main__":
    cli_entry()

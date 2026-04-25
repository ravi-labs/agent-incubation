# agent-foundry

**Back-compat shim package for the Arc agent incubation platform.**

Every module under `src/foundry/` is a thin re-export of its canonical home in
the `arc-*` packages. Existing callers of `from foundry.X import Y` keep
working unchanged; new code should use `arc.*` directly.

```python
# Both of these work, and resolve to the same class:
from foundry.scaffold.base import BaseAgent     # legacy path (this package)
from arc.core import BaseAgent                  # canonical path (arc-core)
```

The package's optional-dependency extras (`[aws]`, `[langchain]`, `[langgraph]`,
`[strands]`, `[cdk]`, `[http]`, `[redis]`, `[otel]`, `[encryption]`,
`[enterprise]`) are preserved as install-time aliases — many error messages
across `arc-*` and `tollgate` still print
`pip install 'agent-foundry[aws]'`-style instructions, and those continue to
install the expected transitive packages.

## When to install this

- You depend on legacy `from foundry.X` imports and don't want to migrate yet.
- You want the convenient `pip install 'agent-foundry[enterprise]'` bundle.
- You want the `foundry` CLI script (which is wired through to arc-cli).

## When to skip it

- New code: depend directly on the relevant `arc-*` package(s).
- Greenfield deployments: see the root [README](../README.md).

## What lived here historically

The full original documentation set (architecture, quickstart, engineering
overview, effect reference, etc.) is preserved at
[../docs/foundry-legacy/](../docs/foundry-legacy/). The deployment artifacts
(Dockerfile, ECS task def, CDK stacks) moved to [../deploy/](../deploy/) and
the shared policy YAMLs to [../policies/](../policies/).

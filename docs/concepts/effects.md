# Effects

An **effect** is a typed, named capability that an agent can request.
`participant.communication.send`, `medication.order.place`, `incident.escalate` —
each is an enum value that carries metadata (which tier, what default
decision, what description) and routes through `ControlTower` exactly the
same way regardless of domain.

Effects are how arc replaces "the agent has access to tool X" with "the
agent has declared scope to perform action Y, which the policy may
ALLOW / ASK / DENY at runtime."

> **Code:** [`arc/packages/arc-core/src/arc/core/effects/`](../../arc/packages/arc-core/src/arc/core/effects/)
> **Public API:** `from arc.core import FinancialEffect, ITSMEffect, HealthcareEffect, LegalEffect, ComplianceEffect, EffectTier, DefaultDecision`

---

## Five domains

Effects are grouped by domain. An agent typically uses one domain
(occasionally two for cross-domain workflows).

| Domain | Enum | Use case | Effect count |
|---|---|---|---|
| Financial services | `FinancialEffect` | retirement plans, ERISA, fiduciary | 36 |
| Healthcare | `HealthcareEffect` | care coordination, medication, prior auth | 35 |
| Legal | `LegalEffect` | contract review, UPL, privilege | 38 |
| ITSM | `ITSMEffect` | incident, change, knowledge, email triage | 41 |
| Compliance | `ComplianceEffect` | regulation tracking, gap analysis, audit | 36 |

Adding a new domain is one new enum module + a metadata table. See
[`base.py`](../../arc/packages/arc-core/src/arc/core/effects/base.py) for the contract.

---

## Six tiers

Every effect lives in exactly one tier. Tiers are an ordering of
*sensitivity* — higher tier means more scrutiny.

```python
class EffectTier(int, Enum):
    DATA_ACCESS    = 1   # read participant data, fetch documents
    COMPUTATION    = 2   # score, classify, summarize, analyze
    DRAFT          = 3   # generate text, propose actions, prepare outputs
    OUTPUT         = 4   # send email, post to ticket, deliver to user
    PERSISTENCE    = 5   # write to record-of-truth (CRM, EMR, ticket system)
    SYSTEM_CONTROL = 6   # promote agent stage, suspend, modify policy
```

The progression is read → think → draft → deliver → persist → administer.
Tier 6 is the smallest set — usually only platform-level effects like
`agent.promote` and `agent.suspend`. Most domain effects sit in tiers 1–4.

Tier ordering shows up in policy:

```yaml
rules:
  - tier_min: 4
    decision: ASK     # all OUTPUT and above route to human review by default
```

---

## Default decisions

Every effect has a `default_decision` baked in:

| Decision | Meaning |
|---|---|
| `ALLOW` | Execute immediately, log, continue |
| `ASK` | Pause execution, route to human approver, resume on decision |
| `DENY` | Block unconditionally, audit the attempt, raise `TollgateDenied` |

The default reflects the natural sensitivity of the effect — `ALLOW` for
reads, often `ASK` for outputs that touch participants, `DENY` for
fiduciary advice or medical orders that require a credentialed reviewer.

A YAML policy can **tighten** the default (turn ALLOW into ASK) but
typically not **loosen** it. Hard-denied effects (e.g.,
`legal.advice.render` in the legal taxonomy) cannot be overridden by
policy — that's the whole point of declaring them as DENY at the
metadata level.

---

## The metadata table

Each domain ships an `EFFECT_METADATA` dict mapping every enum value to
its `EffectMeta` record:

```python
@dataclass(frozen=True)
class EffectMeta:
    tier:             EffectTier
    default_decision: DefaultDecision
    description:      str
    rationale:        str = ""    # *why* this is the default
```

Look up metadata at runtime:

```python
from arc.core import effect_meta, FinancialEffect

meta = effect_meta(FinancialEffect.PARTICIPANT_DATA_READ)
print(meta.tier)              # EffectTier.DATA_ACCESS
print(meta.default_decision)  # DefaultDecision.ALLOW
```

Filter by tier or default:

```python
from arc.core import effects_by_tier, effects_requiring_review, EffectTier

# Every effect in DATA_ACCESS across every domain:
data_reads = effects_by_tier(EffectTier.DATA_ACCESS)

# Every effect whose default is ASK (route to human):
ask_effects = effects_requiring_review()
```

---

## Declaring effects on a manifest

The agent's manifest is the *static* declaration of what it can do.
Anything not on this list raises `PermissionError` at runtime — even if
the policy would have allowed it.

```yaml
# manifest.yaml
agent_id: retirement-trajectory
allowed_effects:
  - participant.data.read              # FinancialEffect.PARTICIPANT_DATA_READ
  - retirement.trajectory.compute      # FinancialEffect.RETIREMENT_TRAJECTORY_COMPUTE
  - participant.communication.draft    # FinancialEffect.PARTICIPANT_COMMUNICATION_DRAFT
  - participant.communication.send     # FinancialEffect.PARTICIPANT_COMMUNICATION_SEND
```

The manifest is the upper bound on what the agent can request. The
policy is the runtime gate that decides which of those declared effects
are allowed under the current operating conditions (tenant, time of day,
caller identity, etc.).

---

## Cross-domain effects

Effects from different enums compare by `.value`, so a manifest can
declare effects from multiple domains and the runtime checks are safe:

```yaml
# A care-coordinator agent that touches both healthcare AND scheduling:
allowed_effects:
  - patient.record.read           # HealthcareEffect
  - medication.order.place        # HealthcareEffect
  - calendar.event.create         # ITSMEffect (treated as scheduling)
```

`AgentManifest.allows_effect(effect)` does string-value comparison, not
class-identity comparison — so you can mix and match.

---

## How an effect becomes a tool call

This is the integration point with `BaseAgent`:

```python
from arc.core import BaseAgent, FinancialEffect

class RetirementTrajectoryAgent(BaseAgent):
    async def execute(self, participant_id: str, **kwargs) -> dict:
        # 1. Read participant data — Tier 1, default ALLOW
        data = await self.run_effect(
            effect = FinancialEffect.PARTICIPANT_DATA_READ,
            tool   = "participants",
            action = "get",
            params = {"id": participant_id},
        )

        # 2. Compute trajectory — Tier 2, default ALLOW
        trajectory = await self.run_effect(
            effect = FinancialEffect.RETIREMENT_TRAJECTORY_COMPUTE,
            tool   = "scorer",
            action = "compute",
            params = {"data": data},
        )

        # 3. Send communication — Tier 4, default ASK (human review)
        return await self.run_effect(
            effect = FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
            tool   = "outbound",
            action = "send",
            params = {"to": participant_id, "body": trajectory.advice},
        )
```

Three effects → three audit rows, three policy evaluations. The third
call blocks until a reviewer approves (or until the configured timeout
expires); the agent code doesn't change to switch between sandbox
auto-approval and production human review.

---

## Where to read next

- [`base.py`](../../arc/packages/arc-core/src/arc/core/effects/base.py) — the contract
  every domain enum implements.
- [`financial.py`](../../arc/packages/arc-core/src/arc/core/effects/financial.py) — most
  developed taxonomy, useful as a template.
- [Governance](governance.md) — how `ControlTower` actually evaluates
  these decisions at runtime.
- [Build an agent](../guides/build-an-agent.md) — declaring + using
  effects in a real agent.

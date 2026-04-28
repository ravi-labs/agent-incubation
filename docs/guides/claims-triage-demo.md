# Build & demo a claims-triage agent — end-to-end walkthrough

A complete, runnable demo of one real agent: **`claims-triage`** — the
kind of agent an insurance company would deploy to read incoming
claims, classify them, route to the right adjuster team, and draft an
acknowledgment to the claimant.

This guide takes you from `git clone` to a working agent with a full
audit trail in **about 30 minutes**. No cloud credentials needed —
everything runs in the controlled, audit-equivalent sandbox using
mock data and a deterministic classifier.

> **Audience:** an engineer who'll demo the platform to internal
> stakeholders. The walk-through doubles as the script you can read
> from during the demo. Both **macOS / Linux (bash)** and **Windows
> (PowerShell)** commands are inline at every step.

---

## What you'll build

An agent that handles incoming insurance claims through five stages:

```
INTAKE → CLASSIFY → EXTRACT → ROUTE → DRAFT
```

For each claim:

1. **Read** the claim record from the gateway (in this demo, a fixture
   YAML file; in production, your case-management system).
2. **Classify** the claim type (auto / property / health / liability)
   and severity (S1 critical → S4 routine).
3. **Extract** structured entities (policy number, claim amount,
   incident date, claimant identity).
4. **Detect duplicates** against recent claims.
5. **Route** to the appropriate adjuster team based on type +
   severity + amount.
6. **Draft** an acknowledgment email to the claimant.
7. **Send** the acknowledgment — but **only after policy gates clear**.
   Claims over $50,000 trigger an **ASK** decision: a senior
   adjuster reviews and approves before any communication leaves the
   building.
8. **Log** every decision to the append-only audit trail.

By the end, you'll have:

- A complete agent under `arc/agents/claims-triage/`
- A run that processes 6 sample claims through the full flow
- An audit-log JSONL file with one row per decision (allow, ask, deny)
- A `DecisionReport` HTML you can open in a browser
- Optionally: the agent visible in the ops dashboard's approval queue

---

## Prerequisites

You need: Python ≥ 3.11, git, and a terminal. (Optional: Node.js if
you want to demo the dashboard.)

### Clone and set up the environment

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Clone | `git clone <repo-url> agent-incubation && cd agent-incubation` | `git clone <repo-url> agent-incubation; cd agent-incubation` |
| Install | `./setup.sh` | `.\setup.bat` |
| Activate | `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| Verify | `arc --help` | `arc --help` |

If `arc` resolves and shows the subcommands list, you're ready.

> **PowerShell execution policy.** If `Activate.ps1` is blocked,
> run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`
> once. This is a one-time per-machine setting.

---

## Step 1 — Scaffold the agent (1 min)

The arc CLI generates the directory layout, manifest skeleton, and
agent code template:

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Scaffold | `arc agent new claims-triage --dir arc/agents/` | `arc agent new claims-triage --dir arc\agents\` |
| Inspect | `ls arc/agents/claims-triage/` | `Get-ChildItem arc\agents\claims-triage\` |

You should see four files:

```
arc/agents/claims-triage/
├── manifest.yaml      ← the contract (we'll edit this)
├── policy.yaml        ← the rules ControlTower applies (we'll edit this)
├── agent.py           ← the business logic (we'll replace this)
└── README.md          ← team-facing notes
```

**Why this matters.** Every agent in arc starts identically. The
shape is opinionated by design — compliance reviewers learn one
layout and recognise it everywhere.

---

## Step 2 — Configure the manifest (5 min)

The manifest is the **agent's contract**: identity, declared scope,
allowed effects, lifecycle stage, SLO targets. Compliance reviews the
manifest, not the source code.

Replace `arc/agents/claims-triage/manifest.yaml` with:

```yaml
agent_id:        claims-triage
version:         "0.1.0"
owner:           claims-operations-team
description: >
  Triages incoming insurance claims — classifies type and severity,
  extracts key entities, routes to the appropriate adjuster team,
  and drafts an acknowledgment to the claimant. Claims over $50k
  require senior adjuster review before any communication is sent.

lifecycle_stage: BUILD
environment:     sandbox
status:          active

team_repo:       https://github.com/your-org/arc-claims-triage
arc_version:     ">=0.1.0"

# Effects this agent is permitted to invoke. Anything not on this list
# raises PermissionError at call time — no exceptions, no overrides.
allowed_effects:
  # Read inputs
  - email.read                          # claim arrives as an email
  - user.directory.read                 # look up claimant profile
  # Computation (no side effects)
  - email.classify                      # classify claim type
  - priority.infer                      # infer severity S1-S4
  - entity.extract                      # pull policy_number, amount, etc.
  - duplicate.detect                    # check for dup submissions
  - routing.decide                      # pick the adjuster team
  # Drafts (writeable, not yet sent)
  - ticket.draft                        # draft the acknowledgment
  # Outputs (subject to policy gates — this is where ASK fires)
  - ticket.create                       # create case workflow item
  - participant.communication.send      # send acknowledgment to claimant
  # Bookkeeping
  - triage.log.write                    # internal triage decision log
  - itsm.audit.log.write                # audit row

data_access:
  - claims.intake                       # incoming claims fixture
  - user.directory                      # claimant lookup
  - claims.recent                       # for duplicate detection

policy_path: arc/agents/claims-triage/policy.yaml

# What "good" looks like after 30 days. Reviewed at VALIDATE / GOVERN.
success_metrics:
  - "Triage time per claim < 60 seconds end-to-end"
  - "Routing accuracy ≥ 95% (audited weekly against adjuster judgment)"
  - "Zero unauthorized acknowledgments sent (every >$50k claim reviewed)"
  - "Duplicate-claim catch rate ≥ 90%"

# Service Level Objective — the auto-demotion watcher reads this.
# A sustained breach (3 consecutive evaluations) proposes a demotion.
slo:
  window:        24h
  min_volume:    50                     # don't evaluate below 50 claims/day
  rules:
    - metric:    error_rate
      op:        "<"
      threshold: 0.05                   # < 5% errors
    - metric:    p95_latency_ms
      op:        "<"
      threshold: 60000                  # < 60s p95
  demotion_mode: proposed               # require human approval

tags:
  - insurance
  - claims
  - triage
```

**What's important here.** The manifest *declares* what the agent
will do — it doesn't grant permissions abstractly. Every effect on
`allowed_effects` is a typed name from the platform's taxonomy
(`arc effects list` shows the full vocabulary). If the agent code
tries to invoke an effect not on this list, the platform raises
`PermissionError` before any side effect happens.

---

## Step 3 — Configure the policy (5 min)

The policy is the **YAML compliance layer** ControlTower applies to
each effect. Engineers write the agent; compliance reviews and edits
the policy. Two languages, two authors, one truth.

Replace `arc/agents/claims-triage/policy.yaml` with:

```yaml
# Policy for: claims-triage
# Authored:   claims-operations-team + compliance
# Reviewed:   2026-04-28
# ───────────────────────────────────────────────────────────────────

rules:
  # ── Default: low-stakes claims auto-process ────────────────────
  # Everything not explicitly tightened is ALLOW.

  # ── Tighten high-value claims ──────────────────────────────────
  - resource_type: "ticket.create"
    when:
      params.claim_amount: { gt: 50000 }
    decision: ASK
    reason: >
      Claims valued over $50,000 require senior adjuster review
      before any case workflow item is created (Internal Policy
      §4.2 — high-value claim oversight).

  - resource_type: "participant.communication.send"
    when:
      params.claim_amount: { gt: 50000 }
    decision: ASK
    reason: >
      Acknowledgment for high-value claims must be reviewed by a
      senior adjuster prior to send (Internal Policy §4.2).

  # ── Tighten claims with risk indicators ────────────────────────
  - resource_type: "ticket.create"
    when:
      params.priority: "S1"
    decision: ASK
    reason: >
      S1 (critical-severity) claims require senior adjuster triage
      regardless of amount.

  # ── Hard-deny unsafe operations ────────────────────────────────
  # The agent never creates a case for a claimant whose policy is
  # already flagged for fraud review. The agent's manifest doesn't
  # declare a check for this; the policy enforces it as a safety net.
  - resource_type: "ticket.create"
    when:
      params.fraud_flag: true
    decision: DENY
    reason: >
      Cannot auto-create cases for claimants under active fraud
      review (Compliance Policy §7.1). Route to fraud team manually.
```

**What's important here.** The policy is independently versioned and
independently reviewed. A compliance officer can tighten any rule
without touching the agent code, and the agent picks up the change at
its next run. The audit trail records the policy version that decided
each call, so a compliance review later can reconstruct exactly which
rules applied.

---

## Step 4 — Write the agent code (10 min)

Replace `arc/agents/claims-triage/agent.py` with:

```python
"""
ClaimsTriageAgent — example end-to-end implementation.

Demonstrates the full pattern: read claims via gateway, classify
deterministically (no LLM dependency for the demo), extract entities,
detect duplicates, route, draft, send. Every effect goes through
self.run_effect() so ControlTower sees it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from arc.core import BaseAgent, load_manifest
from arc.core.effects import FinancialEffect, ITSMEffect
from arc.core.gateway import DataRequest, MockGatewayConnector
from arc.core.observability import OutcomeTracker

logger = logging.getLogger(__name__)

POLICY_PATH   = Path(__file__).parent / "policy.yaml"
MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"


# ─── Deterministic mock classifier ────────────────────────────────────────────
# Replaces an LLM call for the demo. Keyword-based, fast, reproducible.

CLAIM_TYPE_KEYWORDS = {
    "auto":       ["car", "vehicle", "auto", "collision", "accident", "totaled"],
    "property":   ["roof", "flood", "fire", "burglary", "house", "home", "damage"],
    "health":     ["injury", "hospital", "medical", "treatment", "surgery"],
    "liability":  ["sued", "lawsuit", "third party", "negligence", "damages"],
}

SEVERITY_KEYWORDS = {
    "S1": ["fatality", "totaled", "major injury", "homeless", "fire"],
    "S2": ["hospitalized", "uninhabitable", "significant", "urgent"],
    "S3": ["minor injury", "repairable", "moderate"],
    "S4": ["routine", "scratch", "small"],
}

ROUTING = {
    ("auto", "S1"):       "auto-major-loss",
    ("auto", "S2"):       "auto-senior-adjusters",
    ("auto", "S3"):       "auto-standard",
    ("auto", "S4"):       "auto-fast-track",
    ("property", "S1"):   "property-major-loss",
    ("property", "S2"):   "property-senior-adjusters",
    ("property", "S3"):   "property-standard",
    ("property", "S4"):   "property-fast-track",
    ("health", "S1"):     "health-critical",
    ("health", "S2"):     "health-senior",
    ("health", "S3"):     "health-standard",
    ("health", "S4"):     "health-routine",
    ("liability", "S1"):  "liability-litigation",
    ("liability", "S2"):  "liability-senior",
    ("liability", "S3"):  "liability-standard",
    ("liability", "S4"):  "liability-routine",
}


def classify_claim_type(text: str) -> str:
    text_lower = text.lower()
    scores = {ct: sum(1 for kw in kws if kw in text_lower)
              for ct, kws in CLAIM_TYPE_KEYWORDS.items()}
    return max(scores, key=scores.get) if any(scores.values()) else "auto"


def classify_severity(text: str, amount: float) -> str:
    text_lower = text.lower()
    for sev in ("S1", "S2", "S3"):
        if any(kw in text_lower for kw in SEVERITY_KEYWORDS[sev]):
            return sev
    # Amount-based fallback
    if amount > 100_000:   return "S1"
    if amount > 25_000:    return "S2"
    if amount > 5_000:     return "S3"
    return "S4"


def extract_entities(claim: dict) -> dict:
    """Pull structured fields from the claim text + envelope."""
    body = claim.get("body", "")
    return {
        "policy_number": claim.get("policy_number", "")
                          or _regex_first(r"POL[-_]?\d+", body),
        "claim_amount":  float(claim.get("claim_amount", 0)
                                or _regex_first(r"\$([\d,]+)", body, "0").replace(",", "")),
        "incident_date": claim.get("incident_date", "")
                          or _regex_first(r"\d{4}-\d{2}-\d{2}", body),
        "claimant":      claim.get("from", "unknown@example.com"),
    }


def _regex_first(pattern: str, text: str, default: str = "") -> str:
    m = re.search(pattern, text)
    return m.group(1) if m and m.groups() else (m.group(0) if m else default)


# ─── The agent ────────────────────────────────────────────────────────────────

class ClaimsTriageAgent(BaseAgent):
    """Triage incoming insurance claims with full governance."""

    async def execute(self, claim_ids: list[str]) -> dict:
        results = {"processed": 0, "drafted": 0, "deferred": 0, "errors": 0}

        for cid in claim_ids:
            try:
                await self._process_one(cid, results)
            except Exception as exc:
                logger.error("claim %s failed: %s", cid, exc)
                results["errors"] += 1

        logger.info("triage complete: %s", results)
        return results

    async def _process_one(self, cid: str, results: dict) -> None:
        results["processed"] += 1

        # ── Step 1: read the claim ──────────────────────────────────────
        claim_resp = await self.gateway.fetch(DataRequest(
            source="claims.intake", params={"claim_id": cid},
        ))
        claim = claim_resp.data.get(cid)
        if not claim:
            logger.warning("claim %s not found", cid)
            return

        await self.run_effect(
            effect=ITSMEffect.EMAIL_READ,
            tool="claims-intake", action="read",
            params={"claim_id": cid},
            intent_action="read_claim",
            intent_reason=f"Read incoming claim {cid} for triage",
        )

        # ── Step 2: classify type ───────────────────────────────────────
        text = (claim.get("subject", "") + " " + claim.get("body", ""))
        claim_type = await self.run_effect(
            effect=ITSMEffect.EMAIL_CLASSIFY,
            tool="classifier", action="classify",
            params={"claim_id": cid},
            intent_action="classify_claim_type",
            intent_reason=f"Determine claim type for {cid}",
            exec_fn=lambda: classify_claim_type(text),
        )

        # ── Step 3: extract entities ────────────────────────────────────
        entities = await self.run_effect(
            effect=ITSMEffect.ENTITY_EXTRACT,
            tool="entity-extractor", action="extract",
            params={"claim_id": cid},
            intent_action="extract_claim_entities",
            intent_reason=f"Extract policy, amount, date for {cid}",
            exec_fn=lambda: extract_entities(claim),
        )

        # ── Step 4: infer severity ──────────────────────────────────────
        severity = await self.run_effect(
            effect=ITSMEffect.PRIORITY_INFER,
            tool="severity-classifier", action="infer",
            params={"claim_id": cid, "amount": entities["claim_amount"]},
            intent_action="infer_severity",
            intent_reason=f"Determine severity (S1-S4) for {cid}",
            exec_fn=lambda: classify_severity(text, entities["claim_amount"]),
        )

        # ── Step 5: duplicate detection ─────────────────────────────────
        recent = (await self.gateway.fetch(DataRequest(
            source="claims.recent",
            params={"policy_number": entities["policy_number"]},
        ))).data or {}
        is_dup = await self.run_effect(
            effect=ITSMEffect.DUPLICATE_DETECT,
            tool="duplicate-detector", action="check",
            params={"claim_id": cid, "policy_number": entities["policy_number"]},
            intent_action="check_duplicate",
            intent_reason=f"Check {cid} against recent claims for same policy",
            exec_fn=lambda: bool(recent.get("duplicates", [])),
        )
        if is_dup:
            logger.info("claim %s is a duplicate — skipping further triage", cid)
            return

        # ── Step 6: routing ─────────────────────────────────────────────
        team = await self.run_effect(
            effect=ITSMEffect.ROUTING_DECIDE,
            tool="router", action="decide",
            params={"claim_id": cid, "type": claim_type, "severity": severity},
            intent_action="route_claim",
            intent_reason=f"Pick adjuster team for {cid}",
            exec_fn=lambda: ROUTING.get((claim_type, severity), "general"),
        )

        # ── Step 7: draft acknowledgment ────────────────────────────────
        draft = await self.run_effect(
            effect=ITSMEffect.TICKET_DRAFT,
            tool="message-generator", action="draft",
            params={"claim_id": cid, "team": team},
            intent_action="draft_acknowledgment",
            intent_reason=f"Draft initial acknowledgment for claimant {entities['claimant']}",
            exec_fn=lambda: {
                "to":      entities["claimant"],
                "subject": f"We've received your claim {cid}",
                "body":    (
                    f"Hi,\n\n"
                    f"We've received your claim ({cid}, policy "
                    f"{entities['policy_number']}). It has been routed to "
                    f"our {team} team and you'll hear from an adjuster within "
                    f"24 hours. Reference number: {cid}.\n\n"
                    f"This is an acknowledgment only — no decision has been "
                    f"made on your claim yet."
                ),
            },
        )
        results["drafted"] += 1

        # ── Step 8: create case workflow + send acknowledgment ──────────
        # Both gated — high-value claims trigger ASK per policy.
        from tollgate.types import Decision  # for catching DEFERRED
        try:
            await self.run_effect(
                effect=ITSMEffect.TICKET_CREATE,
                tool="case-system", action="create",
                params={
                    "claim_id":      cid,
                    "team":          team,
                    "claim_amount":  entities["claim_amount"],
                    "priority":      severity,
                    "fraud_flag":    claim.get("fraud_flag", False),
                },
                intent_action="create_case",
                intent_reason=f"Open case for {cid} routed to {team}",
            )

            await self.run_effect(
                effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
                tool="email-sender", action="send",
                params={
                    "claim_id":     cid,
                    "claim_amount": entities["claim_amount"],
                    **draft,
                },
                intent_action="send_acknowledgment",
                intent_reason=f"Send claim acknowledgment to {entities['claimant']}",
                metadata={"channel": "email"},
            )
        except Exception as exc:
            # Policy may have DEFERRED (ASK) — that's not an error,
            # it's the platform doing its job.
            cls_name = type(exc).__name__
            if "Defer" in cls_name or "Pending" in cls_name:
                logger.info("claim %s deferred for human review (high value)", cid)
                results["deferred"] += 1
            else:
                raise

        # ── Step 9: log the triage decision ─────────────────────────────
        await self.run_effect(
            effect=ITSMEffect.TRIAGE_LOG_WRITE,
            tool="triage-log", action="write",
            params={
                "claim_id": cid, "type": claim_type, "severity": severity,
                "team":     team,
                "amount":   entities["claim_amount"],
            },
            intent_action="log_triage",
            intent_reason=f"Record triage decision for {cid}",
        )

        # Outcome event for SLO tracking
        if self.tracker:
            await self.tracker.record(
                agent_id="claims-triage", event_type="claim_triaged",
                data={
                    "claim_id": cid, "type": claim_type, "severity": severity,
                    "amount":   entities["claim_amount"],
                    "team":     team, "status": "ok",
                    "latency_ms": 100,   # mocked for the demo
                },
            )


# ─── Wiring ───────────────────────────────────────────────────────────────────
# Standalone runnable when invoked directly. The harness builder is the
# canonical path; this main() is for quick sanity checks.

async def main():
    from arc.harness import HarnessBuilder

    fixture_path = Path(__file__).parent / "fixtures" / "claims.yaml"

    agent = (
        HarnessBuilder(
            manifest = MANIFEST_PATH,
            policy   = POLICY_PATH,
        )
        .with_fixtures(fixture_path)
        .with_tracker(str(Path(__file__).parent / "outcomes.jsonl"))
        .build(ClaimsTriageAgent)
    )

    results = await agent.execute(claim_ids=[
        "CL-001", "CL-002", "CL-003", "CL-004", "CL-005", "CL-006",
    ])
    print("\nResults:", results)

    report = agent.harness_report()
    report.print()

    html_path = Path(__file__).parent / "decisions.html"
    html_path.write_text(report.to_html())
    print(f"\nHTML report: {html_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
```

**What's important here.** Notice every action — read, classify,
extract, route, draft, create, send, log — flows through
`self.run_effect()`. There's no shortcut. ControlTower sees every
call; the audit log captures every decision. The agent's business
logic is the part inside `exec_fn=lambda: ...` — everything around
it is the gate-stack.

---

## Step 5 — Add fixture data (3 min)

Six sample claims that exercise the different paths (low-value
auto-ack, high-value ASK, S1 critical, duplicate, fraud-flagged):

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Make dir | `mkdir -p arc/agents/claims-triage/fixtures` | `New-Item -ItemType Directory -Force arc\agents\claims-triage\fixtures` |

Create `arc/agents/claims-triage/fixtures/claims.yaml`:

```yaml
# Six claims exercising different policy paths.

claims.intake:
  CL-001:
    id:             CL-001
    from:           jane.doe@example.com
    subject:        "Auto claim — minor fender bender"
    body: |
      Hi, I had a small collision in a parking lot yesterday.
      Minor scratch on the bumper. Policy POL-12345.
      Claim amount estimate: $1,200.
      Incident date: 2026-04-22.
    policy_number:  POL-12345
    claim_amount:   1200
    incident_date:  2026-04-22

  CL-002:
    id:             CL-002
    from:           bob.smith@example.com
    subject:        "Property damage — kitchen fire"
    body: |
      We had a kitchen fire last night. House is uninhabitable.
      Significant damage. Policy POL-99887.
      Estimated damages: $85,000.
      Incident date: 2026-04-26.
    policy_number:  POL-99887
    claim_amount:   85000           # >$50k → ASK fires
    incident_date:  2026-04-26

  CL-003:
    id:             CL-003
    from:           carol@example.com
    subject:        "Auto claim — major collision"
    body: |
      Highway accident. Vehicle totaled. Major injury to driver,
      hospitalized. Policy POL-44321.
      Claim estimate: $42,000.
      Incident date: 2026-04-25.
    policy_number:  POL-44321
    claim_amount:   42000
    incident_date:  2026-04-25

  CL-004:
    id:             CL-004
    from:           dan@example.com
    subject:        "Health claim — minor treatment"
    body: |
      Minor injury, outpatient treatment. Policy POL-77001.
      Amount: $850.
      Incident date: 2026-04-21.
    policy_number:  POL-77001
    claim_amount:   850
    incident_date:  2026-04-21

  CL-005:
    id:             CL-005
    from:           eve@example.com
    subject:        "DUPLICATE — Auto claim same as CL-003"
    body: |
      Duplicate submission of the highway accident. Policy POL-44321.
    policy_number:  POL-44321         # same as CL-003 → duplicate detected
    claim_amount:   42000

  CL-006:
    id:             CL-006
    from:           frank@example.com
    subject:        "Liability claim"
    body: |
      Third party lawsuit. Negligence claim. Policy POL-33010.
      Damages sought: $120,000.
      Incident date: 2026-04-18.
    policy_number:  POL-33010
    claim_amount:   120000           # >$50k → ASK fires
    incident_date:  2026-04-18
    fraud_flag:     true             # → DENY at ticket.create per policy

# User directory — claimant lookups
user.directory:
  jane.doe@example.com:    { tier: standard, vip: false }
  bob.smith@example.com:   { tier: gold,     vip: true  }
  carol@example.com:       { tier: standard, vip: false }
  dan@example.com:         { tier: standard, vip: false }
  eve@example.com:         { tier: standard, vip: false }
  frank@example.com:       { tier: standard, vip: false }

# Recent-claims index (for duplicate detection)
claims.recent:
  POL-12345: { duplicates: [] }
  POL-99887: { duplicates: [] }
  POL-44321: { duplicates: ["CL-003"] }   # CL-005 hits this
  POL-77001: { duplicates: [] }
  POL-33010: { duplicates: [] }
```

Six claims. Three exercise gates: **CL-002** triggers ASK (high-value
property), **CL-005** hits the duplicate path, **CL-006** is DENIED
(fraud flag). The other three sail through ALLOW.

---

## Step 6 — Validate the manifest (1 min)

Catch typos and missing fields before running:

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Validate | `arc agent validate arc/agents/claims-triage/manifest.yaml --strict` | `arc agent validate arc\agents\claims-triage\manifest.yaml --strict` |

You should see `✓ manifest valid` followed by a summary table.

> **Common gotcha.** If validation fails with "unknown effect," check
> the spelling against `arc effects list | grep <name>`. Effects are
> typed enums; one wrong character and the platform refuses to load.

---

## Step 7 — Run it in the sandbox (3 min)

This is the headline moment. The harness wires real `ControlTower` +
real `YamlPolicyEvaluator` + real audit sink — only the gateway is
mocked. Same code paths as production, no production exposure.

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Run | `python arc/agents/claims-triage/agent.py` | `python arc\agents\claims-triage\agent.py` |

Expected output (abridged):

```
INFO arc.core.agent: claim CL-001 processed → auto / S4 / auto-fast-track
INFO arc.core.agent: claim CL-002 deferred for human review (high value)
INFO arc.core.agent: claim CL-003 processed → auto / S2 / auto-senior-adjusters
INFO arc.core.agent: claim CL-004 processed → health / S4 / health-routine
INFO arc.core.agent: claim CL-005 is a duplicate — skipping further triage
INFO arc.core.agent: claim CL-006 errored at ticket.create: TollgateDenied
       reason: Cannot auto-create cases for claimants under active fraud review

Results: {'processed': 6, 'drafted': 5, 'deferred': 1, 'errors': 1}
```

The `DecisionReport` prints next, summarising every audit row by
outcome. An HTML version lands at
`arc/agents/claims-triage/decisions.html` — open it in a browser for
the shareable view.

---

## Step 8 — Inspect the audit trail (3 min)

Every decision — ALLOW, ASK, DENY — landed in a JSONL file.

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Find audit | `find . -name "*_audit.jsonl" -mtime -1` | `Get-ChildItem -Recurse -Filter "*_audit.jsonl" \| Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-1) }` |
| Count rows | `wc -l <path>` | `(Get-Content <path>).Count` |
| Inspect denials | `grep '"outcome":"denied"' <path> \| head -3` | `Select-String '"outcome":"denied"' <path> \| Select-Object -First 3` |

Each row is structured:

```json
{
  "timestamp":        "2026-04-28T14:31:08Z",
  "agent_id":         "claims-triage",
  "manifest_version": "claims-triage@0.1.0",
  "policy_version":   "...",
  "intent":           {"action": "create_case", "reason": "..."},
  "request":          {"resource_type": "ticket.create", "params": {...}},
  "decision":         "DENY",
  "reason":           "Cannot auto-create cases for claimants under active fraud review",
  "approver":         "system",
  "approval_ms":      0
}
```

**Talk track for the demo.** This is the *primary compliance artifact*.
Auditors don't read code; they read this log. Every row has the
agent, the manifest version that was approved, the policy version
that decided, the intent, the parameters, the outcome, the reason,
and the timing. Reconstructing what happened to a specific claim is
a one-line `grep`.

---

## Step 9 — Walk through the lifecycle (3 min)

The agent shipped at `BUILD`. Promote it through the gates:

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Inspect current | `arc agent list --dir arc/agents` | `arc agent list --dir arc\agents` |
| Promote → VALIDATE | `arc agent promote arc/agents/claims-triage/manifest.yaml --to VALIDATE` | `arc agent promote arc\agents\claims-triage\manifest.yaml --to VALIDATE` |
| Promote → GOVERN | (same with `--to GOVERN`) | (same with `--to GOVERN`) |
| Promote → SCALE (dry-run) | (same with `--to SCALE --dry-run`) | (same with `--to SCALE --dry-run`) |

Each promotion writes a row to the promotion audit log. **Talk track:**
the dry-run shows the platform requires an explicit confirmation
before flipping to production — no accidental SCALE promotions.

---

## Step 10 — Demo the human approval path (5 min)

This is the moment audiences remember. Open the dashboard, see the
two high-value claims (CL-002, CL-006) sitting in the approval queue.

**Terminal A — backend:**

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Start API | `arc platform serve --port 8000` | `arc platform serve --port 8000` |

**Terminal B — frontend (one-time setup):**

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| `cd` | `cd arc/packages/arc-platform/frontend` | `cd arc\packages\arc-platform\frontend` |
| Install (1st time) | `npm install` | `npm install` |
| Run ops UI | `npm run dev:ops` | `npm run dev:ops` |

Open <http://localhost:5173> and click **Approvals**. The DEFERRED
decisions for CL-002 / CL-006 appear with full context: claim id,
amount, requested action, policy reason. Click **Approve** on one —
the manifest stage flips, the audit row updates, and the
acknowledgment goes out. **One round-trip; full audit; no email
chains.**

---

## Step 11 — Demo auto-demotion (5 min, optional)

Show what happens when the agent's quality regresses in production.
Seed errors into the outcomes log, run the watcher three times, see
the demotion proposal land in the approval queue.

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Seed errors | (see Python block below) | (same Python block below) |

Python (works on both platforms):

```python
import json, datetime
from pathlib import Path

now  = datetime.datetime.now(datetime.timezone.utc).isoformat()
out  = Path("arc/agents/claims-triage/outcomes.jsonl")
with out.open("a") as f:
    for i in range(60):
        f.write(json.dumps({
            "agent_id":   "claims-triage",
            "event_type": "claim_triaged",
            "data":       {"status": "error", "latency_ms": 75000},
            "timestamp":  now,
        }) + "\n")
print(f"Seeded 60 error events to {out}")
```

Then run the watcher (any platform):

```
arc agent watch \
    --registry      arc/agents \
    --outcomes      arc/agents/claims-triage/outcomes.jsonl \
    --audit         /tmp/promotion_audit.jsonl \
    --breach-state  /tmp/breach_state.jsonl \
    --approvals     /tmp/pending_approvals.jsonl \
    --consecutive   3
```

Run it three times in a row. You'll see:

- Run 1: `breach-pending  1/3 consecutive breaches`
- Run 2: `breach-pending  2/3 consecutive breaches`
- Run 3: `proposed        SCALE → GOVERN`

Refresh the dashboard; a new **Approvals** entry appears with
`kind: demotion`. **Talk track:** the platform doesn't quietly demote
agents. It proposes; a human resolves. Default is safe.

---

## Step 12 — Cleanup / restart

To replay the demo from scratch:

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Reset agent | `rm -rf arc/agents/claims-triage` | `Remove-Item -Recurse -Force arc\agents\claims-triage` |
| Reset audits | `find . -name "*_audit.jsonl" -delete; rm -f /tmp/breach_state.jsonl /tmp/pending_approvals.jsonl /tmp/promotion_audit.jsonl` | `Get-ChildItem -Recurse -Filter "*_audit.jsonl" \| Remove-Item; Remove-Item -Force /tmp/breach_state.jsonl, /tmp/pending_approvals.jsonl, /tmp/promotion_audit.jsonl -ErrorAction SilentlyContinue` |

Then re-run from Step 1.

---

## Cheat sheet — the demo arc

| Step | What it shows | Key talking point |
|---|---|---|
| 1. Scaffold | Every agent starts identically | "Compliance learns one shape, recognises it everywhere" |
| 2. Manifest | Declared scope is the contract | "There is no agent without a manifest" |
| 3. Policy | Compliance edits this, not code | "Two languages, two authors, one truth" |
| 4. Code | Every action through `run_effect` | "No back doors, no exceptions" |
| 7. Run | Sandbox = audit-equivalent | "Same governance stack as production, mock connectors" |
| 8. Audit | Compliance's primary artifact | "Auditors read this log, not your code" |
| 10. Approval | Human in the loop, not in the way | "One click; full audit; no email chains" |
| 11. Demotion | Self-healing without surprise | "Platform proposes; human resolves" |

---

## Q&A — what tends to come up

**"What stops the agent from importing `boto3` directly and bypassing
the gate?"** — Convention + code review, not a sandbox. arc enforces
governance by making `run_effect` the path of least resistance and
catching deviations at PR time. For tighter sandboxing (separate
process, restricted container) layer a runtime sandbox underneath.

**"Can compliance update the policy without redeploying the agent?"**
— Yes. Policy YAML is loaded fresh on agent start. Hot-reload is on
the roadmap; today a policy change requires a restart (about a
minute). The manifest version doesn't change — only the policy
version on the audit row updates.

**"What if the LLM (or in this demo, the classifier) makes a
mistake?"** — The audit log captures the decision and its inputs.
Add an `OutcomeEvent` for "claim_misrouted" or similar; the
auto-demotion watcher fires on sustained mistakes. This is what makes
agent regressions a *recoverable* problem instead of a silent one.

**"How is this different from just adding logging to my LangChain
agent?"** — Three things: (1) typed effects, so policy can target
specific actions, not match strings; (2) the lifecycle pipeline, so
agents *earn* production with explicit gates; (3) the manifest as a
contract, so compliance reviews scope before code review. Logging
records what happened. arc records what was *decided* and *why*.

---

## Where to read next

- [Architecture diagrams](../architecture-diagrams.md) — pull diagram
  4 (one effect call) up on the shared screen during Step 7.
- [Lifecycle](../concepts/lifecycle.md) — deep dive on the 6-stage
  pipeline + auto-demotion.
- [Build an agent](build-an-agent.md) — the long-form version of
  this guide for engineers writing their first agent.
- [Demo plan](demo.md) — the generic platform demo (less specific,
  walks through the 6-stage pipeline conceptually).
- [Roadmap](../roadmap.md) — what's shipped vs in flight.

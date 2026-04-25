"""
Care Coordinator Agent — Example Implementation

Demonstrates arc governance using HealthcareEffect (HIPAA taxonomy).

This agent:
  1. Reads patient records and claims data via Gateway (Tier 1 — ALLOW)
  2. Identifies care gaps against clinical guidelines (Tier 2 — ALLOW)
  3. Scores patients by gap severity (Tier 2 — ALLOW)
  4. Drafts personalised outreach messages (Tier 3 — ALLOW)
  5. Routes high-severity patients to human review queue (Tier 4 — ALLOW)
  6. Sends low-severity outreach automatically (Tier 4 — ALLOW by policy)
  7. Attempts a hard-deny effect to demonstrate unconditional blocking

Key governance properties shown:
  - HealthcareEffect enum used throughout (HIPAA-grounded vocabulary)
  - ASK pattern: high-severity outreach suspends until clinician approves
  - Hard-deny: clinical.order.execute is blocked unconditionally
  - Manifest declared scope: only listed effects can be invoked
  - Audit JSONL: every decision recorded for HIPAA compliance

Run:
    cd arc
    python examples/care_coordinator/agent.py
"""

import asyncio
import logging
from pathlib import Path

from arc.core.gateway import MockGatewayConnector
from arc.core.gateway import DataRequest
from arc.core.observability import OutcomeTracker
from arc.core.effects import HealthcareEffect
from arc.core import BaseAgent, load_manifest
from tollgate import (
    ApprovalOutcome,
    ControlTower,
    JsonlAuditSink,
    YamlPolicyEvaluator,
)


class _SandboxApprover:
    """Always-approve approver for sandbox / demo runs.

    In production, swap this for CliApprover (interactive) or
    AsyncQueueApprover (workflow-integrated) so that ASK decisions
    are routed to a real human reviewer.
    """

    async def request_approval_async(self, _agent_ctx, _intent, _tool_request, _hash, _reason):
        return ApprovalOutcome.APPROVED

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

POLICY_PATH   = Path(__file__).parent / "policy.yaml"
MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"


# ─── Synthetic Patient Data (sandbox) ────────────────────────────────────────

SAMPLE_PATIENTS = {
    "pt-001": {
        "id":        "pt-001",
        "name":      "Anita",
        "age":       58,
        "conditions": ["type-2-diabetes", "hypertension"],
        "last_a1c_days_ago":     320,   # overdue (guideline: every 90 days)
        "last_mammogram_days_ago": 410, # overdue (guideline: annual)
        "last_bp_check_days_ago":   45,
        "medications": ["metformin", "lisinopril"],
    },
    "pt-002": {
        "id":        "pt-002",
        "name":      "James",
        "age":       44,
        "conditions": ["asthma"],
        "last_a1c_days_ago":       None,  # not applicable
        "last_mammogram_days_ago": None,  # not applicable
        "last_bp_check_days_ago":  180,   # overdue
        "medications": ["albuterol"],
    },
    "pt-003": {
        "id":        "pt-003",
        "name":      "Rosa",
        "age":       62,
        "conditions": ["type-2-diabetes", "hyperlipidemia"],
        "last_a1c_days_ago":     85,    # within guideline
        "last_mammogram_days_ago": 340,  # approaching overdue
        "last_bp_check_days_ago":  20,
        "medications": ["metformin", "atorvastatin"],
    },
}

CLINICAL_GUIDELINES = {
    "a1c_interval_days":       90,
    "mammogram_interval_days": 365,
    "bp_check_interval_days":  180,
}


# ─── Care Gap Logic ───────────────────────────────────────────────────────────

def identify_care_gaps(patient: dict, guidelines: dict) -> list[dict]:
    """Identify which preventive care items are overdue for a patient."""
    gaps = []

    if patient["last_a1c_days_ago"] is not None:
        if patient["last_a1c_days_ago"] > guidelines["a1c_interval_days"]:
            overdue_days = patient["last_a1c_days_ago"] - guidelines["a1c_interval_days"]
            gaps.append({
                "type":        "a1c_check",
                "overdue_days": overdue_days,
                "severity":    "high" if overdue_days > 180 else "medium",
                "message":     f"HbA1c test overdue by {overdue_days} days",
            })

    if patient["last_mammogram_days_ago"] is not None:
        if patient["last_mammogram_days_ago"] > guidelines["mammogram_interval_days"]:
            overdue_days = patient["last_mammogram_days_ago"] - guidelines["mammogram_interval_days"]
            gaps.append({
                "type":        "mammogram",
                "overdue_days": overdue_days,
                "severity":    "high" if overdue_days > 180 else "medium",
                "message":     f"Annual mammogram overdue by {overdue_days} days",
            })

    if patient["last_bp_check_days_ago"] > guidelines["bp_check_interval_days"]:
        overdue_days = patient["last_bp_check_days_ago"] - guidelines["bp_check_interval_days"]
        gaps.append({
            "type":        "bp_check",
            "overdue_days": overdue_days,
            "severity":    "medium",
            "message":     f"Blood pressure check overdue by {overdue_days} days",
        })

    return gaps


def score_patient_risk(gaps: list[dict]) -> dict:
    """Aggregate gap severity into a patient-level risk score."""
    if not gaps:
        return {"score": 0, "level": "none", "gap_count": 0}
    high_count   = sum(1 for g in gaps if g["severity"] == "high")
    medium_count = sum(1 for g in gaps if g["severity"] == "medium")
    score = high_count * 3 + medium_count * 1
    return {
        "score":       score,
        "level":       "high" if high_count > 0 else "medium",
        "gap_count":   len(gaps),
        "high_gaps":   high_count,
        "medium_gaps": medium_count,
    }


def draft_outreach_message(patient: dict, gaps: list[dict], risk: dict) -> dict:
    """Draft a personalised care gap outreach message."""
    gap_list = "\n".join(f"  • {g['message']}" for g in gaps)
    body = (
        f"Hi {patient['name']},\n\n"
        f"Our records show you may be due for the following preventive care items:\n"
        f"{gap_list}\n\n"
        f"Staying up to date on these helps manage your health more effectively. "
        f"Please call your care team or use the patient portal to schedule these appointments.\n\n"
        f"This is an automated reminder from your care coordination team."
    )
    return {
        "patient_id":   patient["id"],
        "channel":      "secure-message",
        "body":         body,
        "severity":     risk["level"],
        "gap_count":    risk["gap_count"],
    }


# ─── Agent Implementation ─────────────────────────────────────────────────────

class CareCoordinatorAgent(BaseAgent):
    """
    Care Coordinator Agent.

    For each patient, this agent:
      1. Reads patient record and clinical guidelines (HealthcareEffect — Tier 1)
      2. Identifies care gaps (Tier 2 — ALLOW)
      3. Scores patient risk (Tier 2 — ALLOW)
      4. Drafts personalised outreach (Tier 3 — ALLOW)
      5. Routes high-severity to human review queue (Tier 4 — ALLOW)
      6. Sends low-severity outreach (Tier 4 — ALLOW per policy)
      7. Demonstrates hard-deny blocking of clinical.order.execute
    """

    async def execute(self, patient_ids: list[str], demo_hard_deny: bool = False) -> dict:
        results = {
            "processed": 0,
            "gaps_found": 0,
            "auto_sent": 0,
            "escalated_to_review": 0,
            "errors": 0,
        }

        for pid in patient_ids:
            try:
                await self._process_patient(pid, results)
            except Exception as e:
                logger.error("Failed to process patient %s: %s", pid, e)
                results["errors"] += 1

        # ── Hard-deny demonstration ───────────────────────────────────────────
        # Attempt to execute a clinical order — this is unconditionally blocked
        # regardless of the manifest or policy, demonstrating the hard-deny layer.
        if demo_hard_deny:
            await self._demo_hard_deny()

        logger.info("Run complete: %s", results)
        return results

    async def _process_patient(self, pid: str, results: dict) -> None:
        results["processed"] += 1

        # Step 1 — Read patient record (Tier 1: ALLOW)
        p_resp = await self.gateway.fetch(DataRequest(
            source="patient.records",
            params={"patient_id": pid},
        ))
        patient = p_resp.data.get(pid)
        if not patient:
            logger.warning("Patient %s not found", pid)
            return

        # Step 2 — Read clinical guidelines (Tier 1: ALLOW)
        g_resp = await self.gateway.fetch(DataRequest(
            source="clinical.guidelines",
            params={},
        ))
        guidelines = g_resp.data

        # Step 3 — Identify care gaps (Tier 2: ALLOW)
        gaps = await self.run_effect(
            effect=HealthcareEffect.CARE_GAP_IDENTIFY,
            tool="gap_engine",
            action="identify",
            params={"patient_id": pid},
            intent_action="identify_care_gaps",
            intent_reason=f"Identify preventive care gaps for patient {pid}",
            exec_fn=lambda: identify_care_gaps(patient, guidelines),
        )

        if not gaps:
            logger.info("No care gaps for patient %s", pid)
            return

        results["gaps_found"] += len(gaps)

        # Step 4 — Risk stratification (Tier 2: ALLOW)
        risk = await self.run_effect(
            effect=HealthcareEffect.RISK_STRATIFICATION_COMPUTE,
            tool="risk_scorer",
            action="score",
            params={"patient_id": pid, "gaps": gaps},
            intent_action="stratify_risk",
            intent_reason=f"Compute care gap risk score for patient {pid}",
            exec_fn=lambda: score_patient_risk(gaps),
        )

        logger.info(
            "Patient %s — %d gap(s), risk level: %s",
            pid, risk["gap_count"], risk["level"],
        )

        # Step 5 — Draft outreach message (Tier 3: ALLOW — internal only)
        draft = await self.run_effect(
            effect=HealthcareEffect.CLINICAL_SUMMARY_DRAFT,
            tool="message_drafter",
            action="draft",
            params={"patient_id": pid, "gaps": gaps, "risk": risk},
            intent_action="draft_outreach",
            intent_reason=f"Draft care gap outreach for patient {pid}",
            exec_fn=lambda: draft_outreach_message(patient, gaps, risk),
        )

        if risk["level"] == "high":
            # Step 6a — High severity: route to human review queue (ASK path)
            # The policy routes care.gap.alert.send with severity=high to ASK.
            # Add to review queue first so the clinician has context.
            await self.run_effect(
                effect=HealthcareEffect.HUMAN_REVIEW_QUEUE_ADD,
                tool="review_queue",
                action="add",
                params={
                    "patient_id":  pid,
                    "reason":      "High-severity care gap — clinician review required",
                    "draft":       draft,
                    "risk":        risk,
                },
                intent_action="escalate_to_review",
                intent_reason=f"Escalate high-severity patient {pid} for clinical review",
            )
            results["escalated_to_review"] += 1
            logger.info("Patient %s escalated to human review queue", pid)

        # Step 6b — Send outreach (Tier 4: ALLOW for low severity, ASK for high)
        # AutoApprover auto-approves ASK decisions in sandbox — in production
        # this would suspend the agent until a clinician approves.
        await self.run_effect(
            effect=HealthcareEffect.CARE_GAP_ALERT_SEND,
            tool="secure_messaging",
            action="send",
            params=draft,
            intent_action="send_care_gap_alert",
            intent_reason=f"Send care gap outreach to patient {pid}",
            metadata={"severity": risk["level"]},
        )

        if risk["level"] != "high":
            results["auto_sent"] += 1

        # Step 7 — Log the interaction (Tier 5: ALLOW)
        await self.run_effect(
            effect=HealthcareEffect.CARE_INTERACTION_LOG_WRITE,
            tool="interaction_log",
            action="write",
            params={
                "patient_id": pid,
                "gap_count":  risk["gap_count"],
                "risk_level": risk["level"],
                "action":     "outreach_sent",
            },
            intent_action="log_interaction",
            intent_reason="Record care gap interaction for quality tracking",
        )

        await self.log_outcome("care_gap_outreach", {
            "patient_id": pid,
            "gap_count":  risk["gap_count"],
            "risk_level": risk["level"],
        })

    async def _demo_hard_deny(self) -> None:
        """
        Demonstrate the hard-deny layer.

        HealthcareEffect.CLINICAL_ORDER_EXECUTE is on the hard-deny list —
        blocked unconditionally regardless of manifest or policy configuration.
        This shows that even if a developer accidentally adds it to a manifest,
        the framework will raise PermissionError before execution.
        """
        logger.info("\n── Hard-Deny Demonstration ─────────────────────────────")
        logger.info(
            "Attempting HealthcareEffect.CLINICAL_ORDER_EXECUTE "
            "(hard-deny — should be blocked unconditionally)..."
        )
        try:
            # First add it to allowed_effects temporarily to show the gate
            # still blocks it (the hard-deny check runs before manifest check)
            self.manifest.allowed_effects.append(
                HealthcareEffect.CLINICAL_ORDER_EXECUTE
            )
            await self.run_effect(
                effect=HealthcareEffect.CLINICAL_ORDER_EXECUTE,
                tool="ehr_system",
                action="execute_order",
                params={"order_type": "lab", "patient_id": "pt-001"},
                intent_action="execute_clinical_order",
                intent_reason="Attempt blocked by hard-deny layer",
            )
        except (PermissionError, Exception) as e:
            logger.info("✓ Hard-deny blocked: %s", e)
        finally:
            # Clean up
            if HealthcareEffect.CLINICAL_ORDER_EXECUTE in self.manifest.allowed_effects:
                self.manifest.allowed_effects.remove(
                    HealthcareEffect.CLINICAL_ORDER_EXECUTE
                )


# ─── Wiring ───────────────────────────────────────────────────────────────────

def build_agent() -> CareCoordinatorAgent:
    """Wire up the Care Coordinator with its policy, gateway, and tower."""
    manifest = load_manifest(MANIFEST_PATH)

    policy   = YamlPolicyEvaluator(POLICY_PATH)
    approver = _SandboxApprover()   # swap CliApprover or AsyncQueueApprover for production
    audit    = JsonlAuditSink("care_coordinator_audit.jsonl")
    tower    = ControlTower(policy=policy, approver=approver, audit=audit)

    gateway = MockGatewayConnector({
        "patient.records":     SAMPLE_PATIENTS,
        "clinical.guidelines": CLINICAL_GUIDELINES,
    })

    tracker = OutcomeTracker(path="care_coordinator_outcomes.jsonl")

    return CareCoordinatorAgent(
        manifest=manifest,
        tower=tower,
        gateway=gateway,
        tracker=tracker,
    )


async def main():
    print("=" * 60)
    print("Care Coordinator Agent — arc demo")
    print("Taxonomy: HealthcareEffect (HIPAA)")
    print("=" * 60)

    agent = build_agent()

    results = await agent.execute(
        patient_ids=["pt-001", "pt-002", "pt-003"],
        demo_hard_deny=True,
    )

    print("\n── Results ─────────────────────────────────────────────")
    for k, v in results.items():
        print(f"  {k}: {v}")

    if agent.tracker:
        print("\n── Outcome Summary ─────────────────────────────────────")
        print(agent.tracker.summary())

    print("\nAudit log written to: care_coordinator_audit.jsonl")
    print("Generate HTML report: arc-audit care_coordinator_audit.jsonl")


if __name__ == "__main__":
    asyncio.run(main())

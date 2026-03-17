---
name: Effect RFC
about: Request a new effect be added to the financial services taxonomy
title: "[Effect RFC] <effect.value>"
labels: effect-rfc, needs-compliance-review
assignees: ''
---

## Effect RFC — `<effect.value>`

> An RFC (Request for Comment) is required before any new effect is added to the
> `FinancialEffect` taxonomy. New effects become part of the platform's shared
> vocabulary and require compliance review. Fill in all sections before requesting
> a review.

---

### 1. Proposed Effect Value

```
<category>.<subcategory>.<action>
```

Follow the existing naming convention (e.g., `participant.data.read`, `compliance.finding.emit.high`).

### 2. Which Team Is Requesting This?

Team: `<team-name>`
Agent that will use it: `<agent-id>`
Contact: @github-handle

### 3. Why Does This Effect Need to Exist?

Describe the business need. What action does this effect represent? Why can't an existing effect cover it?

### 4. Does a Suitable Effect Already Exist?

Run `foundry effects list` and verify that no existing effect covers this use case.

- [ ] I have reviewed the full effect taxonomy
- [ ] No existing effect covers this use case because: `<reason>`

### 5. Proposed Effect Metadata

| Field | Value |
|-------|-------|
| **Tier** | `1 / 2 / 3 / 4 / 5 / 6` |
| **Tier name** | `Data Access / Computation / Draft / Output / Persistence / System Control` |
| **Base effect** | `READ / WRITE / NOTIFY / DELETE` |
| **Default decision** | `ALLOW / ASK / DENY` |
| **Requires human review** | `Yes / No` |
| **Audit required** | `Yes (always)` |
| **Description** | One sentence describing what this effect does |

### 6. Regulatory Considerations

Does this effect touch any of the following? Check all that apply and explain:

- [ ] Participant PII or account data (GLBA, ERISA privacy)
- [ ] Financial transactions (ERISA §406 prohibited transactions)
- [ ] External communications to participants (FINRA Rule 2210)
- [ ] Advisor routing or suitability (FINRA Rule 2111)
- [ ] Plan configuration (ERISA §402)
- [ ] Compliance findings or regulatory reporting
- [ ] None of the above

**Explanation:** `<explain any checked items>`

### 7. Proposed Default Decision Rationale

Justify the proposed default decision:

- **If ALLOW:** Why is this effect safe to auto-permit without human review?
- **If ASK:** Under what conditions should this require human review? Can it ever be ALLOW?
- **If DENY:** Why should this never be permitted? Is it an absolute prohibition or context-dependent?

### 8. YAML Policy Rule

Proposed rule for `policies/financial_services/defaults.yaml`:

```yaml
- resource_type: "<effect.value>"
  decision: ALLOW | ASK | DENY
  reason: >
    <Regulatory reasoning for this decision>
  regulatory_basis: "<ERISA/DOL/FINRA citation>"
```

### 9. Implementation Notes

Any notes on how this effect should be used, what parameters it expects, or how agents should invoke it.

---

### Review Checklist (Platform Team)

- [ ] Effect value follows naming convention
- [ ] Tier assignment is appropriate
- [ ] Default decision is consistent with similar effects
- [ ] Regulatory considerations have been addressed
- [ ] YAML policy rule is syntactically valid
- [ ] No existing effect already covers this

### Compliance Sign-Off (Required)

- [ ] Regulatory basis reviewed and documented
- [ ] Default decision appropriate under ERISA/DOL/FINRA
- [ ] Hard deny classification verified if applicable

**Compliance officer:** @handle
**Platform owner:** @handle

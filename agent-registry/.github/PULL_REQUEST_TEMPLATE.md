## Agent Registration PR

**Agent ID:** `<agent-id>`
**Owner Team:** `<team-name>`
**Team Repo:** `<https://github.com/your-org/your-repo>`
**Lifecycle Stage:** `GOVERN / SCALE`

---

## Pre-submission Checklist

- [ ] `arc agent validate --strict` passes locally
- [ ] Agent is at lifecycle stage GOVERN or above
- [ ] `team_repo` is set in manifest
- [ ] `foundry_version` is set in manifest
- [ ] Success metrics are defined and measurable
- [ ] Policy file exists at the path declared in manifest

---

## Effect Declaration Review

List all declared effects and why each is needed:

| Effect | Justification |
|--------|---------------|
| `<effect.value>` | `<why needed>` |

Are any Tier 4 (Output) or Tier 6 (System Control) effects declared?
- [ ] No Tier 4/6 effects
- [ ] Yes — explained in the table above

---

## Data Access Review

What data sources does this agent access?

| Data Source | Access Type | Purpose |
|-------------|-------------|---------|
| `<source>` | Read | `<why>` |

Does the data access follow minimum-necessary principle?
- [ ] Yes — only accesses data required for stated purpose

---

## Compliance Officer Review

> *Completed by compliance officer during PR review*

- [ ] Agent purpose is clearly described and appropriate
- [ ] Effect scope is minimal and justified
- [ ] Policy overrides are reviewed and approved
- [ ] No prohibited patterns (per erisa.yaml) are implemented
- [ ] Data access is minimum-necessary
- [ ] Audit trail coverage is adequate for Tier 3+ effects
- [ ] Success metrics are defined (required for SCALE)

**Compliance officer:** @handle
**Review date:**
**Notes:**

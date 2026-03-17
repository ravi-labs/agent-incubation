## Summary

Brief description of what this PR does.

## Type of Change

- [ ] New agent (add to `examples/` or `agents/` for registry submission)
- [ ] Agent update (manifest, policy, or implementation change)
- [ ] Platform change (taxonomy, scaffold, gateway, observability)
- [ ] Policy update (defaults.yaml or erisa.yaml)
- [ ] Documentation
- [ ] Bug fix
- [ ] Other

---

## Agent Changes (fill in if adding/modifying an agent)

**Agent ID:** `<agent-id>`
**Owner team / repo:** `<team-repo-url>`
**Lifecycle stage:** `DISCOVER / SHAPE / BUILD / VALIDATE / GOVERN / SCALE`

### Effects Added or Changed

List any effects added, removed, or changed and why:

| Effect | Before | After | Reason |
|--------|--------|-------|--------|
| | | | |

### Policy Changes

Describe any policy overrides added or changed:

### Success Metrics

Are success metrics defined and measurable?

- [ ] Yes — metrics are specific and measurable
- [ ] Not yet (acceptable for DISCOVER / SHAPE stages)

---

## Platform Changes (fill in if modifying shared infrastructure)

### Effect Taxonomy Changes

If adding a new effect, attach the completed [Effect RFC](.github/ISSUE_TEMPLATE/effect-rfc.md):

- New effect value: `<effect.value>`
- Tier: `1 / 2 / 3 / 4 / 5 / 6`
- Default decision: `ALLOW / ASK / DENY`
- RFC issue: #

### Policy Changes

If modifying `defaults.yaml` or `erisa.yaml`:
- What changed and why
- Regulatory basis (if applicable)
- Impact on existing registered agents

---

## Checklist

- [ ] `foundry agent validate <manifest-path> --strict` passes
- [ ] Tests added / updated for agent or platform changes
- [ ] CI passes (lint, tests, manifest validation, smoke tests)
- [ ] Policy path in manifest resolves to an existing file
- [ ] `team_repo` is set in manifest
- [ ] Success metrics are defined (VALIDATE stage and above)

---

## Compliance Review (required for Stage GOVERN → SCALE promotion)

> *This section is completed by the reviewing compliance officer.*

- [ ] Declared effects are consistent with agent description
- [ ] Policy overrides are justified and do not loosen ERISA/DOL hard denies
- [ ] Data access scope is minimum-necessary
- [ ] Success metrics are defined and measurable
- [ ] No prohibited patterns are implemented (per erisa.yaml)
- [ ] Audit trail requirements are met (Tier 3+ effects have log writes)

**Compliance officer sign-off:** @reviewer-handle

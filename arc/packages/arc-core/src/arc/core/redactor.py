"""arc.core.redactor — pattern-based PII redaction for audit + LLM paths.

Why this exists
---------------
Arc audits everything an agent does. That trail is operationally valuable
(you can reconstruct what happened), but it's also a *liability surface*:
free-text fields like an email body can carry SSNs, account numbers,
dates of birth, routing numbers — straight into the audit log, the
operational logging stack (Datadog / CloudWatch / Splunk), and the LLM
prompts the agent assembles.

This module is the bright line between "agent code in our trust boundary
sees the real value" and "values leaving the boundary are redacted." Two
boundaries are surfaced explicitly:

  1. **Audit sinks.** Wrap any ``AuditSink`` with ``RedactingAuditSink``;
     all params + metadata + reason text get redacted before write.

  2. **LLM provider boundary.** The LLM clients in ``arc.connectors`` and
     the ``governed_chat_model`` adapter call ``Redactor.redact_text``
     before sending prompts to Bedrock / OpenAI / Vertex / etc. — third-
     party providers may log prompts; PII shouldn't leave our trust
     boundary into theirs.

The system of record (Pega, ServiceNow) intentionally gets the
*unredacted* value because that's its job — the case storage is the
legitimate destination.

Design choices
--------------
- **Pattern-based, not NER.** Regex patterns are auditable; ML-based
  named-entity recognition is opaque + flaky. Compliance reviewers can
  read the patterns and decide whether they're sufficient.
- **Default pattern set is conservative.** Only universally-sensitive
  shapes (SSN, credit card, US bank routing, email-shaped strings) ship
  by default. Domain-specific patterns (account numbers, plan IDs)
  are added per agent via configuration.
- **Replacement is labelled, not blank.** ``[REDACTED-SSN]`` not just
  ``****`` — keeps the audit trail readable while removing the value.
- **Recursive on dicts and lists.** Nested fields (``params.claimant.ssn``)
  get redacted just like top-level ones.
- **Never raises.** A redactor that crashes on malformed input is worse
  than one that occasionally misses; this module logs and passes
  through on any internal error.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ── Default patterns ────────────────────────────────────────────────────────
#
# Each pattern has a name (used in the replacement label), a compiled
# regex, and a "scope" — `text` (anywhere in a string) vs `whole` (only
# matches a string that's entirely the pattern). The `whole` scope is
# useful for fields where the value is the SSN, not a sentence containing
# one.

# US SSN — 9 digits with optional separators. The strict pattern (with
# separators) is much higher signal than 9 bare digits, which can match
# many things (zip+4, account suffixes). We ship the strict pattern as
# default and include the bare-digit form as an opt-in.
_SSN_DASHED   = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_SSN_BARE     = re.compile(r"\b\d{9}\b")

# Credit card (13–19 digits, optional separators). Loose match — Luhn
# check would be more accurate but more work; this is good enough for
# redaction (false positives just over-redact, which is the safer side).
_CREDIT_CARD  = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

# US bank routing number — 9 digits in the format common in ABA. Heuristic.
_ROUTING      = re.compile(r"\bABA[\s:#-]*(\d{9})\b", re.IGNORECASE)

# Email — the canonical PII redaction case for many compliance frameworks.
_EMAIL        = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# US phone — area code + exchange + line. Loose; doesn't try to validate.
_PHONE        = re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")


@dataclass(frozen=True)
class Pattern:
    """One redaction pattern.

    ``label`` appears inside ``[REDACTED-...]`` in the output, so it should
    be short + ALL_CAPS. ``regex`` is a compiled pattern. ``replacement``
    overrides the default ``[REDACTED-LABEL]`` for cases where the
    surrounding text needs special handling (e.g. preserve the last 4
    digits of a card).
    """
    label:       str
    regex:       re.Pattern[str]
    replacement: str | None = None

    def apply(self, text: str) -> str:
        repl = self.replacement or f"[REDACTED-{self.label}]"
        return self.regex.sub(repl, text)


# Default pattern set — ships with arc-core. Designed to be safe to apply
# unconditionally to free-text fields (email bodies, audit reason fields,
# LLM prompts).
DEFAULT_PATTERNS: tuple[Pattern, ...] = (
    Pattern("SSN",          _SSN_DASHED),
    Pattern("CREDIT_CARD",  _CREDIT_CARD),
    Pattern("ROUTING",      _ROUTING),
    Pattern("EMAIL",        _EMAIL),
    Pattern("PHONE",        _PHONE),
)

# Optional — opt in by passing ``include_bare_ssn=True``. Bare 9-digit
# patterns over-redact (zip+4 codes, some account suffixes) but catch
# SSNs that arrive without separators.
BARE_SSN_PATTERN = Pattern("SSN", _SSN_BARE)


# ── Redactor ────────────────────────────────────────────────────────────────


@dataclass
class Redactor:
    """Apply a configurable pattern set to strings, dicts, and lists.

    Construct once per process; reuse across audit + LLM call sites. Thread-safe
    (compiled regex objects are stateless after compile).
    """

    patterns:    tuple[Pattern, ...] = DEFAULT_PATTERNS
    extra:       tuple[Pattern, ...] = field(default_factory=tuple)
    sensitive_keys: frozenset[str]   = frozenset(
        {
            "password", "token", "secret", "authorization", "api_key",
            "key", "ssn", "social_security_number", "tax_id",
            "account_number", "routing_number", "card_number",
        }
    )
    telemetry: Any = None  # optional arc.core.Telemetry for match counts

    def __post_init__(self) -> None:
        self._all_patterns = tuple(self.patterns) + tuple(self.extra)

    # ── Top-level redact API ────────────────────────────────────────────

    def redact(self, value: Any) -> Any:
        """Recursively redact a string, dict, list, or scalar.

        Strings: pattern matches replaced. Dicts: keys whose name is in
        ``sensitive_keys`` get the value replaced wholesale; other values
        recursed into. Lists: element-wise. Other types: returned as-is.
        """
        try:
            return self._redact(value)
        except Exception as exc:
            # Never crash the audit / LLM path because of a redactor bug.
            logger.warning("redactor failed; passing through: %s", exc)
            return value

    def redact_text(self, text: str) -> str:
        """Redact a string only. Faster path — skips the recursive walk."""
        return self._redact_string(text)

    # ── internals ──────────────────────────────────────────────────────

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_string(value)
        if isinstance(value, dict):
            return {
                k: (
                    f"[REDACTED-{k.upper()}]"
                    if isinstance(k, str) and k.lower() in self.sensitive_keys
                    else self._redact(v)
                )
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            redacted = [self._redact(v) for v in value]
            return tuple(redacted) if isinstance(value, tuple) else redacted
        return value

    def _redact_string(self, text: str) -> str:
        out = text
        for p in self._all_patterns:
            repl = p.replacement or f"[REDACTED-{p.label}]"
            out, n = p.regex.subn(repl, out)
            if n and self.telemetry is not None:
                # Best-effort match counter — never raise. The pattern
                # label is bounded cardinality (the configured set) so
                # safe to use as a tag.
                try:
                    self.telemetry.count(
                        "arc.redaction.match",
                        float(n),
                        tags={"pattern": p.label},
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("redactor_telemetry_emit_failed err=%s", exc)
        return out


# ── RedactingAuditSink — wraps any AuditSink with redaction ─────────────────


class RedactingAuditSink:
    """Audit sink wrapper that redacts params + metadata + reason before write.

    Compose with any AuditSink that has a ``record`` (or equivalent) method::

        from tollgate import JsonlAuditSink
        from arc.core import RedactingAuditSink, Redactor

        sink = RedactingAuditSink(
            inner    = JsonlAuditSink(log_path="audit.jsonl"),
            redactor = Redactor(),
        )
        tower = ControlTower(policy=..., approver=..., audit=sink)

    The redactor walks every str / dict / list in the audit row before
    handoff to the inner sink. Top-level metadata fields known to be
    structured-but-non-sensitive (timestamp, agent_id, decision) are
    left alone so dashboards still index them correctly.
    """

    # Fields that should never be redacted — they're structural, not data.
    STRUCTURAL_FIELDS: frozenset[str] = frozenset({
        "timestamp", "ts", "agent_id", "manifest_version", "policy_version",
        "decision", "outcome", "approval_ms", "tool", "action", "effect",
        "resource_type",
    })

    def __init__(self, inner: Any, redactor: Redactor | None = None) -> None:
        self.inner    = inner
        self.redactor = redactor or Redactor()

    # ── AuditSink Protocol — async path ─────────────────────────────────

    async def record(self, *args: Any, **kwargs: Any) -> Any:
        """Pass-through that redacts dict / str args before delegating.

        We don't constrain the inner sink's signature — we redact
        positional + keyword arguments uniformly. Tollgate's AuditSink
        Protocol takes a structured ``AuditEvent`` dataclass; whatever
        the shape, dict-shaped fields land redacted.
        """
        redacted_args   = tuple(self._redact_arg(a) for a in args)
        redacted_kwargs = {k: self._redact_arg(v) for k, v in kwargs.items()}

        record = getattr(self.inner, "record", None)
        if record is None:
            raise AttributeError(
                f"{type(self.inner).__name__} does not implement .record()"
            )

        # Support both async and sync inner sinks.
        result = record(*redacted_args, **redacted_kwargs)
        if hasattr(result, "__await__"):
            return await result
        return result

    # ── Pass-through for any other method on the inner sink ─────────────

    def __getattr__(self, name: str) -> Any:
        # Called only when normal attribute lookup fails — so this catches
        # methods like ``close()``, ``flush()`` etc. that we don't redact.
        return getattr(self.inner, name)

    # ── internals ──────────────────────────────────────────────────────

    def _redact_arg(self, value: Any) -> Any:
        """Redact a single argument. Preserve dataclass shape if present."""
        # Plain dict / list / str — straight through the redactor.
        if isinstance(value, (str, dict, list, tuple)):
            return self.redactor.redact(value)

        # Dataclass-like (Tollgate's AuditEvent etc.) — redact fields the
        # redactor can reach without touching the structural ones.
        if hasattr(value, "__dataclass_fields__"):
            from dataclasses import fields, replace
            updates: dict[str, Any] = {}
            for f in fields(value):
                if f.name in self.STRUCTURAL_FIELDS:
                    continue
                v = getattr(value, f.name)
                if isinstance(v, (str, dict, list, tuple)):
                    updates[f.name] = self.redactor.redact(v)
            return replace(value, **updates) if updates else value

        # Anything else: pass through.
        return value


__all__ = [
    "Pattern",
    "Redactor",
    "RedactingAuditSink",
    "DEFAULT_PATTERNS",
    "BARE_SSN_PATTERN",
]

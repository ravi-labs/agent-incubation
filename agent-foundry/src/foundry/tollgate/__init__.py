from .anomaly_detector import AnomalyAlert, AnomalyDetector
from .approvals import (
    ApprovalStore,
    Approver,
    AsyncQueueApprover,
    AutoApprover,
    CliApprover,
    InMemoryApprovalStore,
    compute_request_hash,
)
from .audit import AuditSink, CompositeAuditSink, JsonlAuditSink, WebhookAuditSink

# Import cache module
from .cache import CachedGrantStore, CacheEntry, GrantCache
from .circuit_breaker import CircuitBreaker, CircuitState, InMemoryCircuitBreaker
from .context_monitor import ContextIntegrityMonitor, VerificationResult
from .exceptions import (
    TollgateApprovalDenied,
    TollgateConstraintViolation,
    TollgateDeferred,
    TollgateDenied,
    TollgateError,
    TollgateRateLimited,
)
from .explainer import DecisionExplainer, ExplanationResult, RuleMatchResult
from .grants import GrantStore, InMemoryGrantStore
from .helpers import guard, wrap_tool
from .manifest_signing import get_manifest_hash, sign_manifest, verify_manifest
from .network_guard import NetworkGuard
from .playground import PolicyPlayground
from .policy import PolicyEvaluator, YamlPolicyEvaluator
from .policy_testing import PolicyTestRunner, PolicyTestRunResult
from .policy_versioning import (
    InMemoryPolicyVersionStore,
    PolicyDiff,
    PolicyVersion,
    PolicyVersionStore,
    SQLitePolicyVersionStore,
    VersionedPolicyEvaluator,
)
from .rate_limiter import InMemoryRateLimiter, RateLimiter
from .registry import ToolRegistry
from .reputation import (
    AgentReputation,
    EventType,
    InMemoryReputationStore,
    ReputationAuditSink,
    ReputationConfig,
    ReputationEvent,
    ReputationManager,
    ReputationStore,
    SQLiteReputationStore,
)
from .slo import (
    AlertSeverity,
    SLOAlert,
    SLOAuditSink,
    SLOConfig,
    SLOMetrics,
    SLOMonitor,
    SLOType,
)
from .telemetry import TelemetryAuditSink

# Import tenancy module
from .workflow import (
    ApproverSpec,
    EscalationPath,
    InMemoryWorkflowStore,
    SQLiteWorkflowStore,
    StepExecution,
    StepStatus,
    StepType,
    WorkflowBuilder,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowInstance,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStore,
    create_conditional_approval_workflow,
    create_simple_approval_workflow,
    create_two_level_approval_workflow,
)
from .tenancy import (
    InMemoryQuotaStore,
    QuotaEnforcer,
    QuotaExceededError,
    QuotaStore,
    TenantQuotas,
)
from .tower import ControlTower
from .types import (
    AgentContext,
    ApprovalOutcome,
    AuditEvent,
    Decision,
    DecisionType,
    Effect,
    Grant,
    Intent,
    NormalizedToolCall,
    Outcome,
    ToolRequest,
)
from .verification import make_verifier, sign_agent_context, verify_agent_context

# Optional: Ed25519 signing (requires pip install tollgate[encryption])
try:
    from .verification import (
        generate_ed25519_keypair,
        make_ed25519_verifier,
        sign_agent_context_ed25519,
        verify_agent_context_ed25519,
    )

    _HAS_ED25519 = True
except ImportError:
    _HAS_ED25519 = False
    generate_ed25519_keypair = None  # type: ignore[assignment]
    sign_agent_context_ed25519 = None  # type: ignore[assignment]
    verify_agent_context_ed25519 = None  # type: ignore[assignment]
    make_ed25519_verifier = None  # type: ignore[assignment]

# Optional: Security features (requires pip install tollgate[encryption])
try:
    from .security import (
        EncryptedAuditSink,
        EncryptedValue,
        FieldEncryptor,
        ImmutableAuditSink,
    )

    _HAS_SECURITY = True
except ImportError:
    _HAS_SECURITY = False
    FieldEncryptor = None  # type: ignore[assignment, misc]
    EncryptedValue = None  # type: ignore[assignment, misc]
    EncryptedAuditSink = None  # type: ignore[assignment, misc]
    ImmutableAuditSink = None  # type: ignore[assignment, misc]

# Optional: OpenTelemetry metrics and tracing (requires pip install tollgate[otel])
try:
    from .otel import (
        OTelMetricsAuditSink,
        OTelTracingAuditSink,
        TracingContextManager,
        create_otel_sink,
        create_otel_sinks,
        create_otel_tracing_sink,
    )

    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False
    OTelMetricsAuditSink = None  # type: ignore[assignment, misc]
    OTelTracingAuditSink = None  # type: ignore[assignment, misc]
    TracingContextManager = None  # type: ignore[assignment, misc]
    create_otel_sink = None  # type: ignore[assignment]
    create_otel_sinks = None  # type: ignore[assignment]
    create_otel_tracing_sink = None  # type: ignore[assignment]

__version__ = "1.4.0"

__all__ = [
    "ControlTower",
    "AgentContext",
    "Intent",
    "ToolRequest",
    "NormalizedToolCall",
    "Decision",
    "DecisionType",
    "Effect",
    "Grant",
    "GrantStore",
    "AuditEvent",
    "Outcome",
    "ApprovalOutcome",
    "ApprovalStore",
    "Approver",
    "InMemoryApprovalStore",
    "AsyncQueueApprover",
    "AutoApprover",
    "CliApprover",
    "compute_request_hash",
    "AuditSink",
    "JsonlAuditSink",
    "CompositeAuditSink",
    "WebhookAuditSink",
    "ToolRegistry",
    "PolicyEvaluator",
    "YamlPolicyEvaluator",
    "InMemoryGrantStore",
    "TollgateError",
    "TollgateDenied",
    "TollgateApprovalDenied",
    "TollgateDeferred",
    "TollgateRateLimited",
    "TollgateConstraintViolation",
    "RateLimiter",
    "InMemoryRateLimiter",
    "CircuitBreaker",
    "InMemoryCircuitBreaker",
    "CircuitState",
    "NetworkGuard",
    "sign_manifest",
    "verify_manifest",
    "get_manifest_hash",
    "sign_agent_context",
    "verify_agent_context",
    "make_verifier",
    "PolicyTestRunner",
    "PolicyTestRunResult",
    "ContextIntegrityMonitor",
    "VerificationResult",
    "AnomalyDetector",
    "AnomalyAlert",
    "TelemetryAuditSink",
    "wrap_tool",
    "guard",
    # Decision Explainer
    "DecisionExplainer",
    "ExplanationResult",
    "RuleMatchResult",
    # Policy Playground
    "PolicyPlayground",
    # Caching
    "GrantCache",
    "CachedGrantStore",
    "CacheEntry",
    # Tenancy / Quotas
    "TenantQuotas",
    "QuotaStore",
    "InMemoryQuotaStore",
    "QuotaEnforcer",
    "QuotaExceededError",
    # Policy Versioning
    "PolicyVersion",
    "PolicyDiff",
    "PolicyVersionStore",
    "InMemoryPolicyVersionStore",
    "SQLitePolicyVersionStore",
    "VersionedPolicyEvaluator",
    # SLO Monitoring
    "SLOType",
    "AlertSeverity",
    "SLOConfig",
    "SLOAlert",
    "SLOMetrics",
    "SLOMonitor",
    "SLOAuditSink",
    # Agent Reputation
    "EventType",
    "ReputationConfig",
    "ReputationEvent",
    "AgentReputation",
    "ReputationStore",
    "InMemoryReputationStore",
    "SQLiteReputationStore",
    "ReputationManager",
    "ReputationAuditSink",
    # Workflow Orchestration
    "StepType",
    "WorkflowStatus",
    "StepStatus",
    "ApproverSpec",
    "EscalationPath",
    "WorkflowStep",
    "WorkflowDefinition",
    "StepExecution",
    "WorkflowInstance",
    "WorkflowStore",
    "InMemoryWorkflowStore",
    "SQLiteWorkflowStore",
    "WorkflowEngine",
    "WorkflowBuilder",
    "create_simple_approval_workflow",
    "create_two_level_approval_workflow",
    "create_conditional_approval_workflow",
]

# Conditionally add OTel exports
if _HAS_OTEL:
    __all__.extend([
        "OTelMetricsAuditSink",
        "OTelTracingAuditSink",
        "TracingContextManager",
        "create_otel_sink",
        "create_otel_sinks",
        "create_otel_tracing_sink",
    ])

# Conditionally add Ed25519 exports
if _HAS_ED25519:
    __all__.extend([
        "generate_ed25519_keypair",
        "sign_agent_context_ed25519",
        "verify_agent_context_ed25519",
        "make_ed25519_verifier",
    ])

# Conditionally add security exports
if _HAS_SECURITY:
    __all__.extend([
        "FieldEncryptor",
        "EncryptedValue",
        "EncryptedAuditSink",
        "ImmutableAuditSink",
    ])

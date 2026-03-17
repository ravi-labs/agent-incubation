"""Workflow Orchestration for multi-step approval chains.

This module provides workflow orchestration for enterprise agentic frameworks:
- Multi-step approval chains
- Escalation paths with timeouts
- Conditional workflows based on context
- Parallel and sequential step execution
- State persistence and recovery
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class StepType(Enum):
    """Type of workflow step."""

    APPROVAL = "approval"
    CONDITION = "condition"
    ACTION = "action"
    PARALLEL = "parallel"
    ESCALATION = "escalation"


class WorkflowStatus(Enum):
    """Status of a workflow instance."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    DENIED = "denied"
    ESCALATED = "escalated"
    TIMEOUT = "timeout"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(Enum):
    """Status of a workflow step."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class ApproverSpec:
    """Specification for an approver."""

    approver_id: str
    approver_type: str = "user"  # user, group, role
    required: bool = True
    timeout_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "approver_id": self.approver_id,
            "approver_type": self.approver_type,
            "required": self.required,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApproverSpec:
        """Create from dictionary."""
        return cls(
            approver_id=data["approver_id"],
            approver_type=data.get("approver_type", "user"),
            required=data.get("required", True),
            timeout_seconds=data.get("timeout_seconds"),
        )


@dataclass(frozen=True)
class EscalationPath:
    """Escalation path configuration."""

    approvers: tuple[ApproverSpec, ...]
    timeout_seconds: float = 3600.0  # Default 1 hour per level
    max_levels: int = 3

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "approvers": [a.to_dict() for a in self.approvers],
            "timeout_seconds": self.timeout_seconds,
            "max_levels": self.max_levels,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EscalationPath:
        """Create from dictionary."""
        return cls(
            approvers=tuple(
                ApproverSpec.from_dict(a) for a in data.get("approvers", [])
            ),
            timeout_seconds=data.get("timeout_seconds", 3600.0),
            max_levels=data.get("max_levels", 3),
        )


@dataclass(frozen=True)
class WorkflowStep:
    """A single step in a workflow."""

    id: str
    step_type: StepType
    name: str
    config: dict[str, Any] = field(default_factory=dict)
    next_step: str | None = None
    on_failure: str | None = None
    timeout_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "step_type": self.step_type.value,
            "name": self.name,
            "config": self.config,
            "next_step": self.next_step,
            "on_failure": self.on_failure,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStep:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            step_type=StepType(data["step_type"]),
            name=data["name"],
            config=data.get("config", {}),
            next_step=data.get("next_step"),
            on_failure=data.get("on_failure"),
            timeout_seconds=data.get("timeout_seconds"),
        )


@dataclass(frozen=True)
class WorkflowDefinition:
    """Definition of a workflow."""

    id: str
    name: str
    description: str
    steps: tuple[WorkflowStep, ...]
    entry_step: str
    escalation_path: EscalationPath | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "entry_step": self.entry_step,
            "escalation_path": (
                self.escalation_path.to_dict() if self.escalation_path else None
            ),
            "metadata": self.metadata,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowDefinition:
        """Create from dictionary."""
        escalation = data.get("escalation_path")
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            steps=tuple(WorkflowStep.from_dict(s) for s in data.get("steps", [])),
            entry_step=data["entry_step"],
            escalation_path=(
                EscalationPath.from_dict(escalation) if escalation else None
            ),
            metadata=data.get("metadata", {}),
            version=data.get("version", "1.0"),
        )

    def get_step(self, step_id: str) -> WorkflowStep | None:
        """Get a step by ID."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None


@dataclass
class StepExecution:
    """Execution state of a workflow step."""

    step_id: str
    status: StepStatus
    started_at: float | None = None
    completed_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    approver_responses: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
            "approver_responses": self.approver_responses,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepExecution:
        """Create from dictionary."""
        return cls(
            step_id=data["step_id"],
            status=StepStatus(data["status"]),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            result=data.get("result"),
            error=data.get("error"),
            approver_responses=data.get("approver_responses", {}),
        )


@dataclass
class WorkflowInstance:
    """A running instance of a workflow."""

    id: str
    workflow_id: str
    status: WorkflowStatus
    context: dict[str, Any]
    current_step: str | None = None
    step_executions: dict[str, StepExecution] = field(default_factory=dict)
    escalation_level: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "status": self.status.value,
            "context": self.context,
            "current_step": self.current_step,
            "step_executions": {
                k: v.to_dict() for k, v in self.step_executions.items()
            },
            "escalation_level": self.escalation_level,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowInstance:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            workflow_id=data["workflow_id"],
            status=WorkflowStatus(data["status"]),
            context=data.get("context", {}),
            current_step=data.get("current_step"),
            step_executions={
                k: StepExecution.from_dict(v)
                for k, v in data.get("step_executions", {}).items()
            },
            escalation_level=data.get("escalation_level", 0),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            completed_at=data.get("completed_at"),
            result=data.get("result"),
            error=data.get("error"),
        )


class WorkflowStore(Protocol):
    """Protocol for workflow storage."""

    async def save_definition(self, definition: WorkflowDefinition) -> None:
        """Save a workflow definition."""
        ...

    async def get_definition(self, workflow_id: str) -> WorkflowDefinition | None:
        """Get a workflow definition by ID."""
        ...

    async def list_definitions(self) -> list[WorkflowDefinition]:
        """List all workflow definitions."""
        ...

    async def save_instance(self, instance: WorkflowInstance) -> None:
        """Save a workflow instance."""
        ...

    async def get_instance(self, instance_id: str) -> WorkflowInstance | None:
        """Get a workflow instance by ID."""
        ...

    async def list_instances(
        self,
        workflow_id: str | None = None,
        status: WorkflowStatus | None = None,
        limit: int = 100,
    ) -> list[WorkflowInstance]:
        """List workflow instances."""
        ...

    async def get_pending_approvals(
        self, approver_id: str
    ) -> list[tuple[WorkflowInstance, WorkflowStep]]:
        """Get pending approvals for an approver."""
        ...


class InMemoryWorkflowStore:
    """In-memory workflow store for testing."""

    def __init__(self) -> None:
        self._definitions: dict[str, WorkflowDefinition] = {}
        self._instances: dict[str, WorkflowInstance] = {}

    async def save_definition(self, definition: WorkflowDefinition) -> None:
        """Save a workflow definition."""
        self._definitions[definition.id] = definition

    async def get_definition(self, workflow_id: str) -> WorkflowDefinition | None:
        """Get a workflow definition by ID."""
        return self._definitions.get(workflow_id)

    async def list_definitions(self) -> list[WorkflowDefinition]:
        """List all workflow definitions."""
        return list(self._definitions.values())

    async def save_instance(self, instance: WorkflowInstance) -> None:
        """Save a workflow instance."""
        instance.updated_at = time.time()
        self._instances[instance.id] = instance

    async def get_instance(self, instance_id: str) -> WorkflowInstance | None:
        """Get a workflow instance by ID."""
        return self._instances.get(instance_id)

    async def list_instances(
        self,
        workflow_id: str | None = None,
        status: WorkflowStatus | None = None,
        limit: int = 100,
    ) -> list[WorkflowInstance]:
        """List workflow instances."""
        result = []
        for instance in self._instances.values():
            if workflow_id and instance.workflow_id != workflow_id:
                continue
            if status and instance.status != status:
                continue
            result.append(instance)
            if len(result) >= limit:
                break
        return sorted(result, key=lambda x: x.created_at, reverse=True)

    async def get_pending_approvals(
        self, approver_id: str
    ) -> list[tuple[WorkflowInstance, WorkflowStep]]:
        """Get pending approvals for an approver."""
        result = []
        for instance in self._instances.values():
            if instance.status != WorkflowStatus.AWAITING_APPROVAL:
                continue
            if instance.current_step is None:
                continue

            # Find the workflow definition
            definition = self._definitions.get(instance.workflow_id)
            if definition is None:
                continue

            step = definition.get_step(instance.current_step)
            if step is None or step.step_type != StepType.APPROVAL:
                continue

            # Check if this approver is in the list
            approvers = step.config.get("approvers", [])
            for approver in approvers:
                if approver.get("approver_id") == approver_id:
                    result.append((instance, step))
                    break

        return result


class SQLiteWorkflowStore:
    """SQLite-backed workflow store."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workflow_definitions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    data TEXT NOT NULL,
                    version TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workflow_instances (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_step TEXT,
                    escalation_level INTEGER DEFAULT 0,
                    data TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    FOREIGN KEY (workflow_id) REFERENCES workflow_definitions(id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_instances_workflow
                ON workflow_instances(workflow_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_instances_status
                ON workflow_instances(status)
            """)
            conn.commit()
        finally:
            conn.close()

    async def save_definition(self, definition: WorkflowDefinition) -> None:
        """Save a workflow definition."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO workflow_definitions
                (id, name, description, data, version, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    definition.id,
                    definition.name,
                    definition.description,
                    json.dumps(definition.to_dict()),
                    definition.version,
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def get_definition(self, workflow_id: str) -> WorkflowDefinition | None:
        """Get a workflow definition by ID."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT data FROM workflow_definitions WHERE id = ?",
                (workflow_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return WorkflowDefinition.from_dict(json.loads(row[0]))
        finally:
            conn.close()

    async def list_definitions(self) -> list[WorkflowDefinition]:
        """List all workflow definitions."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT data FROM workflow_definitions ORDER BY created_at DESC"
            )
            return [
                WorkflowDefinition.from_dict(json.loads(row[0]))
                for row in cursor.fetchall()
            ]
        finally:
            conn.close()

    async def save_instance(self, instance: WorkflowInstance) -> None:
        """Save a workflow instance."""
        instance.updated_at = time.time()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO workflow_instances
                (id, workflow_id, status, current_step, escalation_level,
                 data, created_at, updated_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    instance.id,
                    instance.workflow_id,
                    instance.status.value,
                    instance.current_step,
                    instance.escalation_level,
                    json.dumps(instance.to_dict()),
                    instance.created_at,
                    instance.updated_at,
                    instance.completed_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def get_instance(self, instance_id: str) -> WorkflowInstance | None:
        """Get a workflow instance by ID."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT data FROM workflow_instances WHERE id = ?",
                (instance_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return WorkflowInstance.from_dict(json.loads(row[0]))
        finally:
            conn.close()

    async def list_instances(
        self,
        workflow_id: str | None = None,
        status: WorkflowStatus | None = None,
        limit: int = 100,
    ) -> list[WorkflowInstance]:
        """List workflow instances."""
        conn = sqlite3.connect(self.db_path)
        try:
            query = "SELECT data FROM workflow_instances WHERE 1=1"
            params: list[Any] = []

            if workflow_id:
                query += " AND workflow_id = ?"
                params.append(workflow_id)
            if status:
                query += " AND status = ?"
                params.append(status.value)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            return [
                WorkflowInstance.from_dict(json.loads(row[0]))
                for row in cursor.fetchall()
            ]
        finally:
            conn.close()

    async def get_pending_approvals(
        self, approver_id: str
    ) -> list[tuple[WorkflowInstance, WorkflowStep]]:
        """Get pending approvals for an approver."""
        conn = sqlite3.connect(self.db_path)
        try:
            # Get all instances awaiting approval
            cursor = conn.execute(
                """
                SELECT wi.data, wd.data
                FROM workflow_instances wi
                JOIN workflow_definitions wd ON wi.workflow_id = wd.id
                WHERE wi.status = ?
            """,
                (WorkflowStatus.AWAITING_APPROVAL.value,),
            )

            result = []
            for row in cursor.fetchall():
                instance = WorkflowInstance.from_dict(json.loads(row[0]))
                definition = WorkflowDefinition.from_dict(json.loads(row[1]))

                if instance.current_step is None:
                    continue

                step = definition.get_step(instance.current_step)
                if step is None or step.step_type != StepType.APPROVAL:
                    continue

                # Check if this approver is in the list
                approvers = step.config.get("approvers", [])
                for approver in approvers:
                    if approver.get("approver_id") == approver_id:
                        result.append((instance, step))
                        break

            return result
        finally:
            conn.close()


class StepHandler(ABC):
    """Base class for step handlers."""

    @abstractmethod
    async def execute(
        self,
        step: WorkflowStep,
        instance: WorkflowInstance,
        context: dict[str, Any],
    ) -> tuple[StepStatus, dict[str, Any] | None, str | None]:
        """Execute a step and return (status, result, error)."""
        ...


class ApprovalStepHandler(StepHandler):
    """Handler for approval steps."""

    def __init__(self, approver_callback: Any | None = None) -> None:
        self.approver_callback = approver_callback
        self._pending_approvals: dict[str, asyncio.Event] = {}
        self._approval_results: dict[str, tuple[bool, str]] = {}

    async def execute(
        self,
        step: WorkflowStep,
        instance: WorkflowInstance,
        context: dict[str, Any],  # noqa: ARG002
    ) -> tuple[StepStatus, dict[str, Any] | None, str | None]:
        """Execute approval step."""
        # This will be called to check if approval is complete
        step_exec = instance.step_executions.get(step.id)
        if step_exec is None:
            return StepStatus.PENDING, None, None

        # Check if all required approvers have responded
        approvers = step.config.get("approvers", [])
        required_approvers = [a for a in approvers if a.get("required", True)]

        all_approved = True
        any_denied = False

        for approver in required_approvers:
            approver_id = approver.get("approver_id", "")
            response = step_exec.approver_responses.get(approver_id)

            if response is None:
                all_approved = False
            elif response == "denied":
                any_denied = True

        if any_denied:
            return StepStatus.FAILED, {"denied": True}, "Approval denied"

        if all_approved:
            return StepStatus.COMPLETED, {"approved": True}, None

        return StepStatus.RUNNING, None, None

    def submit_approval(
        self, instance_id: str, step_id: str, approver_id: str, approved: bool
    ) -> None:
        """Submit an approval decision."""
        key = f"{instance_id}:{step_id}:{approver_id}"
        self._approval_results[key] = (approved, approver_id)
        if key in self._pending_approvals:
            self._pending_approvals[key].set()


class ConditionStepHandler(StepHandler):
    """Handler for condition steps."""

    async def execute(
        self,
        step: WorkflowStep,
        instance: WorkflowInstance,  # noqa: ARG002
        context: dict[str, Any],
    ) -> tuple[StepStatus, dict[str, Any] | None, str | None]:
        """Evaluate a condition."""
        field = step.config.get("field", "")
        operator = step.config.get("operator", "eq")
        value = step.config.get("value")

        # Get the field value from context
        field_value = context.get(field)

        # Evaluate condition
        result = False
        if operator == "eq":
            result = field_value == value
        elif operator == "ne":
            result = field_value != value
        elif operator == "gt":
            result = field_value > value
        elif operator == "lt":
            result = field_value < value
        elif operator == "gte":
            result = field_value >= value
        elif operator == "lte":
            result = field_value <= value
        elif operator == "contains":
            result = value in (field_value or "")
        elif operator == "in":
            result = field_value in (value or [])
        elif operator == "exists":
            result = field_value is not None

        next_key = "on_true" if result else "on_false"
        return (
            StepStatus.COMPLETED,
            {"condition_met": result, "next_step": step.config.get(next_key)},
            None,
        )


class ActionStepHandler(StepHandler):
    """Handler for action steps."""

    def __init__(
        self, action_handlers: dict[str, Any] | None = None
    ) -> None:
        self.action_handlers = action_handlers or {}

    async def execute(
        self,
        step: WorkflowStep,
        instance: WorkflowInstance,
        context: dict[str, Any],
    ) -> tuple[StepStatus, dict[str, Any] | None, str | None]:
        """Execute an action."""
        action_type = step.config.get("action_type", "")
        handler = self.action_handlers.get(action_type)

        if handler is None:
            return (
                StepStatus.FAILED,
                None,
                f"Unknown action type: {action_type}",
            )

        try:
            result = await handler(step, instance, context)
            return StepStatus.COMPLETED, result, None
        except Exception as e:
            return StepStatus.FAILED, None, str(e)


class WorkflowEngine:
    """Engine for executing workflows."""

    def __init__(
        self,
        store: WorkflowStore,
        step_handlers: dict[StepType, StepHandler] | None = None,
    ) -> None:
        self.store = store
        self.step_handlers = step_handlers or {
            StepType.APPROVAL: ApprovalStepHandler(),
            StepType.CONDITION: ConditionStepHandler(),
            StepType.ACTION: ActionStepHandler(),
        }
        self._escalation_tasks: dict[str, asyncio.Task[None]] = {}

    async def register_workflow(self, definition: WorkflowDefinition) -> None:
        """Register a workflow definition."""
        await self.store.save_definition(definition)

    async def start_workflow(
        self,
        workflow_id: str,
        context: dict[str, Any] | None = None,
    ) -> WorkflowInstance:
        """Start a new workflow instance."""
        definition = await self.store.get_definition(workflow_id)
        if definition is None:
            raise ValueError(f"Workflow not found: {workflow_id}")

        instance = WorkflowInstance(
            id=uuid.uuid4().hex[:16],
            workflow_id=workflow_id,
            status=WorkflowStatus.PENDING,
            context=context or {},
            current_step=definition.entry_step,
        )

        await self.store.save_instance(instance)
        return instance

    async def execute_step(
        self,
        instance_id: str,
    ) -> WorkflowInstance:
        """Execute the current step of a workflow."""
        instance = await self.store.get_instance(instance_id)
        if instance is None:
            raise ValueError(f"Instance not found: {instance_id}")

        if instance.status in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        ):
            return instance

        definition = await self.store.get_definition(instance.workflow_id)
        if definition is None:
            raise ValueError(f"Workflow not found: {instance.workflow_id}")

        if instance.current_step is None:
            instance.status = WorkflowStatus.COMPLETED
            instance.completed_at = time.time()
            await self.store.save_instance(instance)
            return instance

        step = definition.get_step(instance.current_step)
        if step is None:
            instance.status = WorkflowStatus.FAILED
            instance.error = f"Step not found: {instance.current_step}"
            await self.store.save_instance(instance)
            return instance

        # Get or create step execution
        if step.id not in instance.step_executions:
            instance.step_executions[step.id] = StepExecution(
                step_id=step.id,
                status=StepStatus.PENDING,
            )

        step_exec = instance.step_executions[step.id]

        # Get the handler for this step type
        handler = self.step_handlers.get(step.step_type)
        if handler is None:
            instance.status = WorkflowStatus.FAILED
            instance.error = f"No handler for step type: {step.step_type}"
            await self.store.save_instance(instance)
            return instance

        # Mark step as running
        if step_exec.status == StepStatus.PENDING:
            step_exec.status = StepStatus.RUNNING
            step_exec.started_at = time.time()
            instance.status = WorkflowStatus.RUNNING
            await self.store.save_instance(instance)

        # Execute the step
        status, result, error = await handler.execute(step, instance, instance.context)

        # Update step execution
        is_terminal = status in (StepStatus.COMPLETED, StepStatus.FAILED)
        instance.step_executions[step.id] = StepExecution(
            step_id=step.id,
            status=status,
            started_at=step_exec.started_at,
            completed_at=time.time() if is_terminal else None,
            result=result,
            error=error,
            approver_responses=step_exec.approver_responses,
        )

        # Handle step completion
        if status == StepStatus.COMPLETED:
            # Determine next step
            next_step = step.next_step
            if result and "next_step" in result:
                next_step = result["next_step"]

            instance.current_step = next_step
            if next_step is None:
                instance.status = WorkflowStatus.COMPLETED
                instance.completed_at = time.time()
                instance.result = result
        elif status == StepStatus.FAILED:
            if step.on_failure:
                instance.current_step = step.on_failure
            else:
                instance.status = WorkflowStatus.FAILED
                instance.error = error
        elif status == StepStatus.RUNNING and step.step_type == StepType.APPROVAL:
            instance.status = WorkflowStatus.AWAITING_APPROVAL

        await self.store.save_instance(instance)
        return instance

    async def run_to_completion(
        self,
        instance_id: str,
        max_steps: int = 100,
    ) -> WorkflowInstance:
        """Run a workflow until it completes or requires approval."""
        for _ in range(max_steps):
            instance = await self.execute_step(instance_id)
            if instance.status in (
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELLED,
                WorkflowStatus.AWAITING_APPROVAL,
            ):
                return instance
        return await self.store.get_instance(instance_id) or instance

    async def submit_approval(
        self,
        instance_id: str,
        approver_id: str,
        approved: bool,
        comment: str | None = None,  # noqa: ARG002 - reserved for future use
    ) -> WorkflowInstance:
        """Submit an approval decision."""
        instance = await self.store.get_instance(instance_id)
        if instance is None:
            raise ValueError(f"Instance not found: {instance_id}")

        if instance.status != WorkflowStatus.AWAITING_APPROVAL:
            raise ValueError(f"Instance not awaiting approval: {instance.status}")

        if instance.current_step is None:
            raise ValueError("No current step")

        # Update the step execution with the approval
        step_exec = instance.step_executions.get(instance.current_step)
        if step_exec is None:
            # Create step execution if it doesn't exist
            step_exec = StepExecution(
                step_id=instance.current_step,
                status=StepStatus.RUNNING,
                started_at=time.time(),
            )

        step_exec.approver_responses[approver_id] = "approved" if approved else "denied"
        instance.step_executions[instance.current_step] = step_exec

        await self.store.save_instance(instance)

        # Continue execution
        result = await self.execute_step(instance_id)

        # If we moved to a new step and it's awaiting approval, execute again to initialize
        if (
            result.status == WorkflowStatus.RUNNING
            and result.current_step
            and result.current_step not in result.step_executions
        ):
            result = await self.execute_step(instance_id)

        return result

    async def cancel_workflow(self, instance_id: str) -> WorkflowInstance:
        """Cancel a workflow instance."""
        instance = await self.store.get_instance(instance_id)
        if instance is None:
            raise ValueError(f"Instance not found: {instance_id}")

        instance.status = WorkflowStatus.CANCELLED
        instance.completed_at = time.time()
        await self.store.save_instance(instance)
        return instance

    async def escalate(self, instance_id: str) -> WorkflowInstance:
        """Escalate a workflow to the next level."""
        instance = await self.store.get_instance(instance_id)
        if instance is None:
            raise ValueError(f"Instance not found: {instance_id}")

        definition = await self.store.get_definition(instance.workflow_id)
        if definition is None or definition.escalation_path is None:
            raise ValueError("No escalation path defined")

        if instance.escalation_level >= definition.escalation_path.max_levels:
            instance.status = WorkflowStatus.TIMEOUT
            instance.error = "Max escalation level reached"
            await self.store.save_instance(instance)
            return instance

        instance.escalation_level += 1
        instance.status = WorkflowStatus.ESCALATED
        await self.store.save_instance(instance)
        return instance

    async def get_pending_approvals(
        self, approver_id: str
    ) -> list[tuple[WorkflowInstance, WorkflowStep]]:
        """Get pending approvals for an approver."""
        return await self.store.get_pending_approvals(approver_id)


# Workflow builder for easier workflow creation


class WorkflowBuilder:
    """Builder for creating workflow definitions."""

    def __init__(self, workflow_id: str, name: str) -> None:
        self.workflow_id = workflow_id
        self.name = name
        self.description = ""
        self.steps: list[WorkflowStep] = []
        self.entry_step: str | None = None
        self.escalation_path: EscalationPath | None = None
        self.metadata: dict[str, Any] = {}
        self.version = "1.0"

    def with_description(self, description: str) -> WorkflowBuilder:
        """Set the description."""
        self.description = description
        return self

    def with_version(self, version: str) -> WorkflowBuilder:
        """Set the version."""
        self.version = version
        return self

    def with_metadata(self, metadata: dict[str, Any]) -> WorkflowBuilder:
        """Set metadata."""
        self.metadata = metadata
        return self

    def add_approval_step(
        self,
        step_id: str,
        name: str,
        approvers: list[ApproverSpec],
        next_step: str | None = None,
        on_failure: str | None = None,
        timeout_seconds: float | None = None,
    ) -> WorkflowBuilder:
        """Add an approval step."""
        step = WorkflowStep(
            id=step_id,
            step_type=StepType.APPROVAL,
            name=name,
            config={"approvers": [a.to_dict() for a in approvers]},
            next_step=next_step,
            on_failure=on_failure,
            timeout_seconds=timeout_seconds,
        )
        self.steps.append(step)
        if self.entry_step is None:
            self.entry_step = step_id
        return self

    def add_condition_step(
        self,
        step_id: str,
        name: str,
        field: str,
        operator: str,
        value: Any,
        on_true: str | None = None,
        on_false: str | None = None,
    ) -> WorkflowBuilder:
        """Add a condition step."""
        step = WorkflowStep(
            id=step_id,
            step_type=StepType.CONDITION,
            name=name,
            config={
                "field": field,
                "operator": operator,
                "value": value,
                "on_true": on_true,
                "on_false": on_false,
            },
        )
        self.steps.append(step)
        if self.entry_step is None:
            self.entry_step = step_id
        return self

    def add_action_step(
        self,
        step_id: str,
        name: str,
        action_type: str,
        action_config: dict[str, Any] | None = None,
        next_step: str | None = None,
        on_failure: str | None = None,
    ) -> WorkflowBuilder:
        """Add an action step."""
        config = {"action_type": action_type}
        if action_config:
            config.update(action_config)

        step = WorkflowStep(
            id=step_id,
            step_type=StepType.ACTION,
            name=name,
            config=config,
            next_step=next_step,
            on_failure=on_failure,
        )
        self.steps.append(step)
        if self.entry_step is None:
            self.entry_step = step_id
        return self

    def with_escalation(
        self,
        approvers: list[ApproverSpec],
        timeout_seconds: float = 3600.0,
        max_levels: int = 3,
    ) -> WorkflowBuilder:
        """Set escalation path."""
        self.escalation_path = EscalationPath(
            approvers=tuple(approvers),
            timeout_seconds=timeout_seconds,
            max_levels=max_levels,
        )
        return self

    def set_entry_step(self, step_id: str) -> WorkflowBuilder:
        """Set the entry step explicitly."""
        self.entry_step = step_id
        return self

    def build(self) -> WorkflowDefinition:
        """Build the workflow definition."""
        if self.entry_step is None:
            raise ValueError("Entry step not set")

        return WorkflowDefinition(
            id=self.workflow_id,
            name=self.name,
            description=self.description,
            steps=tuple(self.steps),
            entry_step=self.entry_step,
            escalation_path=self.escalation_path,
            metadata=self.metadata,
            version=self.version,
        )


# Pre-built workflow templates


def create_simple_approval_workflow(
    workflow_id: str,
    name: str,
    approvers: list[ApproverSpec],
) -> WorkflowDefinition:
    """Create a simple single-approval workflow."""
    return (
        WorkflowBuilder(workflow_id, name)
        .with_description("Simple single-level approval workflow")
        .add_approval_step(
            step_id="approve",
            name="Approval Required",
            approvers=approvers,
        )
        .build()
    )


def create_two_level_approval_workflow(
    workflow_id: str,
    name: str,
    first_level_approvers: list[ApproverSpec],
    second_level_approvers: list[ApproverSpec],
) -> WorkflowDefinition:
    """Create a two-level approval workflow."""
    return (
        WorkflowBuilder(workflow_id, name)
        .with_description("Two-level approval workflow")
        .add_approval_step(
            step_id="level1",
            name="First Level Approval",
            approvers=first_level_approvers,
            next_step="level2",
        )
        .add_approval_step(
            step_id="level2",
            name="Second Level Approval",
            approvers=second_level_approvers,
        )
        .build()
    )


def create_conditional_approval_workflow(
    workflow_id: str,
    name: str,
    condition_field: str,
    condition_value: Any,
    high_risk_approvers: list[ApproverSpec],
    low_risk_approvers: list[ApproverSpec],
) -> WorkflowDefinition:
    """Create a conditional approval workflow based on risk level."""
    return (
        WorkflowBuilder(workflow_id, name)
        .with_description("Conditional approval based on risk level")
        .add_condition_step(
            step_id="check_risk",
            name="Check Risk Level",
            field=condition_field,
            operator="eq",
            value=condition_value,
            on_true="high_risk_approval",
            on_false="low_risk_approval",
        )
        .add_approval_step(
            step_id="high_risk_approval",
            name="High Risk Approval",
            approvers=high_risk_approvers,
        )
        .add_approval_step(
            step_id="low_risk_approval",
            name="Low Risk Approval",
            approvers=low_risk_approvers,
        )
        .set_entry_step("check_risk")
        .build()
    )

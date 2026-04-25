"""
foundry.integrations.bedrock_kb
─────────────────────────────────
Amazon Bedrock Knowledge Base integration for agent-foundry.

Allows agents to retrieve relevant documents from a Bedrock Knowledge Base
as part of their execute() flow. All retrievals go through run_effect()
so they are policy-enforced, audit-logged, and declared in the manifest.

Install:
    pip install "agent-foundry[aws]"

Manifest declaration:

    allowed_effects:
      - knowledge.base.retrieve      # Required

    knowledge_bases:
      - id: "ABCDEFGHIJ"
        name: "plan-documents"
        description: >
          ERISA plan documents, SPDs, and investment policy statements
        max_results: 10

Usage in agent:

    class FiduciaryWatchdogAgent(BaseAgent):

        def __init__(self, manifest, tower, gateway, tracker=None):
            super().__init__(manifest, tower, gateway, tracker)
            self.kb = BedrockKBClient(
                agent=self,
                knowledge_base_id="ABCDEFGHIJ",
            )

        async def execute(self, fund_id: str, **kwargs) -> dict:
            # Retrieve relevant plan documents
            docs = await self.kb.retrieve(
                query=f"investment policy statement for fund {fund_id}",
                intent_action="retrieve_plan_docs",
                intent_reason="Evaluate fund compliance against stated IPS",
            )

            # docs is a list of RetrievedPassage with text, score, metadata
            context = "\\n\\n".join(p.text for p in docs)
            ...
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from arc.core import BaseAgent

logger = logging.getLogger(__name__)


# ── Data model ─────────────────────────────────────────────────────────────────


@dataclass
class RetrievedPassage:
    """A single retrieved passage from a Bedrock Knowledge Base."""
    text:     str
    score:    float
    source:   str          # S3 URI or document URI
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_bedrock_result(cls, result: dict) -> "RetrievedPassage":
        """Parse a single result from Bedrock RetrieveResponse."""
        content  = result.get("content", {})
        location = result.get("location", {})
        meta     = result.get("metadata", {})
        score    = result.get("score", 0.0)

        text = content.get("text", "")

        # Source URI — S3, web, or custom
        s3_location  = location.get("s3Location", {})
        web_location = location.get("webLocation", {})
        source = (
            s3_location.get("uri")
            or web_location.get("url")
            or str(location)
        )

        return cls(text=text, score=score, source=source, metadata=meta)


# ── Client ──────────────────────────────────────────────────────────────────────


class BedrockKBClient:
    """
    Retrieves relevant passages from an Amazon Bedrock Knowledge Base.

    All retrievals are routed through agent.run_effect() — meaning:
      - The KNOWLEDGE_BASE_RETRIEVE effect must be in the agent manifest
      - The policy YAML controls whether retrieval is ALLOW / ASK / DENY
      - Every retrieval is audit-logged with the query, KB ID, and result count

    Args:
        agent:              The BaseAgent instance making the retrieval.
        knowledge_base_id:  The Bedrock Knowledge Base ID (e.g. "ABCDEFGHIJ").
        max_results:        Maximum passages to return per query (default 5).
        min_score:          Minimum relevance score to include (default 0.0).
        region:             AWS region (default from environment).
    """

    def __init__(
        self,
        agent: "BaseAgent",
        knowledge_base_id: str,
        max_results: int = 5,
        min_score: float = 0.0,
        region: str | None = None,
    ):
        self.agent             = agent
        self.knowledge_base_id = knowledge_base_id
        self.max_results       = max_results
        self.min_score         = min_score
        self._region           = region
        self._client: Any      = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3 is not installed. Run: pip install 'agent-foundry[aws]'"
                ) from exc
            self._client = boto3.client(
                "bedrock-agent-runtime",
                region_name=self._region,
            )
        return self._client

    def _retrieve_sync(self, query: str, max_results: int) -> list[RetrievedPassage]:
        """Synchronous retrieval — called via asyncio.to_thread()."""
        client = self._get_client()
        t0 = time.monotonic()

        resp = client.retrieve(
            knowledgeBaseId=self.knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": max_results,
                },
            },
        )

        duration_ms = round((time.monotonic() - t0) * 1000)
        results = resp.get("retrievalResults", [])

        passages = [
            RetrievedPassage.from_bedrock_result(r)
            for r in results
            if r.get("score", 0.0) >= self.min_score
        ]

        logger.debug(
            "kb_retrieve kb=%s query_len=%d results=%d kept=%d duration_ms=%d",
            self.knowledge_base_id, len(query), len(results), len(passages), duration_ms,
        )
        return passages

    async def retrieve(
        self,
        query: str,
        *,
        intent_action: str,
        intent_reason: str,
        max_results: int | None = None,
        metadata: dict | None = None,
    ) -> list[RetrievedPassage]:
        """
        Retrieve relevant passages from the Knowledge Base.

        Routed through agent.run_effect() — policy-enforced and audit-logged.

        Args:
            query:           Natural language query to search the KB.
            intent_action:   Short label for why this retrieval is happening.
            intent_reason:   Detailed justification for the audit trail.
            max_results:     Override the default max_results for this call.
            metadata:        Extra metadata for the audit event.

        Returns:
            List of RetrievedPassage sorted by score (descending).
        """
        from arc.core.effects import FinancialEffect

        _max = max_results or self.max_results

        async def _exec():
            return await asyncio.to_thread(self._retrieve_sync, query, _max)

        passages: list[RetrievedPassage] = await self.agent.run_effect(
            effect=FinancialEffect.KNOWLEDGE_BASE_RETRIEVE,
            tool="bedrock-kb",
            action="retrieve",
            params={
                "knowledge_base_id": self.knowledge_base_id,
                "query_length":      len(query),
                "max_results":       _max,
            },
            intent_action=intent_action,
            intent_reason=intent_reason,
            metadata={
                "knowledge_base_id": self.knowledge_base_id,
                "query_preview":     query[:100],
                **(metadata or {}),
            },
            exec_fn=_exec,
        )

        return sorted(passages, key=lambda p: p.score, reverse=True)

    async def retrieve_and_format(
        self,
        query: str,
        *,
        intent_action: str,
        intent_reason: str,
        max_results: int | None = None,
        separator: str = "\n\n---\n\n",
    ) -> str:
        """
        Retrieve passages and return them as a single formatted string.

        Useful for constructing LLM context windows:

            context = await self.kb.retrieve_and_format(
                query="fund due diligence criteria",
                intent_action="get_ips_context",
                intent_reason="Provide IPS context for compliance evaluation",
            )
            # Pass context to BedrockLLMClient.generate()

        Returns:
            Concatenated passage texts with separator between each.
        """
        passages = await self.retrieve(
            query, intent_action=intent_action, intent_reason=intent_reason,
            max_results=max_results,
        )
        return separator.join(
            f"[Source: {p.source}  Score: {p.score:.3f}]\n{p.text}"
            for p in passages
        )

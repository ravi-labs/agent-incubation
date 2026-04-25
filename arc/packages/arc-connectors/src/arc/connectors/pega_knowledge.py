"""
PegaKnowledgeConnector — Pega Knowledge Buddy (RAG) connector for Arc.

Queries the Pega Knowledge Management REST API to retrieve relevant
articles, answers, and source references for a given question or keywords.

Used as an optional enrichment step in the email triage graph — when
a KB match is found it is attached to the ticket description.

Usage:
    from arc.connectors.pega_knowledge import PegaKnowledgeConnector
    from arc.runtime.config import PegaKnowledgeConfig

    connector = PegaKnowledgeConnector(PegaKnowledgeConfig.from_env())
    results = await connector.query("How do I reset my 2FA device?", max_results=3)
    # results: [{"article_id": ..., "title": ..., "answer": ..., "confidence": ..., "source_url": ...}]
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .base import ArcConnector

logger = logging.getLogger(__name__)

_KB_ARTICLES_PATH = "/prweb/api/PegaKMREST/articles/KMGetArticlesAndPosts"


class PegaKnowledgeConnector(ArcConnector):
    """
    Pega Knowledge Buddy (KM REST API) connector.

    Capabilities:
      - query()            — RAG-style question answering against the KB
      - search_articles()  — keyword search for relevant articles

    Auth: API key (passed as X-KM-API-Key header).
    No token caching needed — API key is static.
    """

    def __init__(self, config: Any):
        """
        Args:
            config: PegaKnowledgeConfig instance with base_url and api_key.
        """
        self._config = config

    # ── Public API ────────────────────────────────────────────────────────────

    async def query(
        self,
        question: str,
        max_results: int = 3,
    ) -> list[dict[str, Any]]:
        """
        Query Pega Knowledge Buddy for relevant answers.

        Submits the question to the Pega KM RAG endpoint and returns
        grounded answers with article references and confidence scores.

        Args:
            question:    Natural language question.
            max_results: Maximum number of results to return (default: 3).

        Returns:
            List of dicts with keys:
              article_id  — Pega article ID
              title       — Article title
              answer      — Excerpt / answer from the article
              confidence  — Relevance score (0.0–1.0)
              source_url  — Direct URL to the article
        """
        url = f"{self._config.base_url}{_KB_ARTICLES_PATH}"
        payload = {
            "query": question,
            "maxResults": max_results,
            "queryType": "RAG",
            "includeAnswers": True,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=self._kb_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        return self._parse_results(data, max_results)

    async def search_articles(
        self,
        keywords: str | list[str],
        category: str | None = None,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Keyword search against the Pega Knowledge Base.

        Args:
            keywords:    Search term string or list of keywords.
            category:    Optional article category filter.
            max_results: Maximum number of results to return (default: 5).

        Returns:
            List of matching article dicts (same shape as query()).
        """
        url = f"{self._config.base_url}{_KB_ARTICLES_PATH}"
        if isinstance(keywords, list):
            keywords = " ".join(keywords)

        payload: dict[str, Any] = {
            "query": keywords,
            "maxResults": max_results,
            "queryType": "KEYWORD",
        }
        if category:
            payload["category"] = category

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=self._kb_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        return self._parse_results(data, max_results)

    # ── GatewayConnector-compatible interface ─────────────────────────────────

    async def _do_fetch(self, source: str, params: dict[str, Any]) -> Any:
        """GatewayConnector fetch interface."""
        if source in ("knowledge.buddy", "kb.query"):
            return await self.query(
                question=params.get("question", params.get("query", "")),
                max_results=params.get("max_results", 3),
            )
        if source == "kb.search":
            return await self.search_articles(
                keywords=params.get("keywords", ""),
                category=params.get("category"),
                max_results=params.get("max_results", 5),
            )
        raise ValueError(f"PegaKnowledgeConnector: unknown source {source!r}")

    async def _do_execute(self, action: str, params: dict[str, Any]) -> Any:
        """Pega Knowledge is read-only — no execute actions."""
        raise ValueError(
            f"PegaKnowledgeConnector: execute not supported (action={action!r}). "
            "Knowledge Buddy is read-only."
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _kb_headers(self) -> dict[str, str]:
        return {
            "X-KM-API-Key": self._config.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _parse_results(data: dict, max_results: int) -> list[dict[str, Any]]:
        """Normalise the Pega KM API response into the standard article shape."""
        articles = data.get("articles", data.get("results", data.get("items", [])))
        results = []
        for item in articles[:max_results]:
            results.append({
                "article_id":  item.get("articleID", item.get("id", "")),
                "title":       item.get("pyArticleTitle", item.get("title", "")),
                "answer":      item.get("pyExcerpt", item.get("answer", item.get("content", ""))),
                "confidence":  float(item.get("relevanceScore", item.get("confidence", 0.0))),
                "source_url":  item.get("articleURL", item.get("url", "")),
            })
        return results

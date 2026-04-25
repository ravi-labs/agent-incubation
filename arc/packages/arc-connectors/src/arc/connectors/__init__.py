"""
arc.connectors — connector library for Arc agents.

Provides connectors for:
  - Microsoft Outlook / Graph API (email)
  - Pega Case Management (ITSM tickets)
  - Pega Knowledge Buddy (RAG knowledge base)
  - ServiceNow Table API (ITSM incidents)
  - MockTicketConnector (in-memory harness testing)

All connectors implement the ArcConnector base class with fetch() and
execute() methods compatible with GatewayConnector, making them
substitutable by MockGatewayConnector in harness mode.

Usage:
    from arc.connectors import (
        OutlookConnector,
        PegaCaseConnector,
        PegaKnowledgeConnector,
        ServiceNowConnector,
        MockTicketConnector,
    )
"""

from .base import ArcConnector, OAuthMixin
from .mock import MockTicketConnector
from .outlook import OutlookConnector
from .pega_case import PegaCaseConnector
from .pega_knowledge import PegaKnowledgeConnector
from .servicenow import ServiceNowConnector

__all__ = [
    "ArcConnector",
    "OAuthMixin",
    "OutlookConnector",
    "PegaCaseConnector",
    "PegaKnowledgeConnector",
    "ServiceNowConnector",
    "MockTicketConnector",
]

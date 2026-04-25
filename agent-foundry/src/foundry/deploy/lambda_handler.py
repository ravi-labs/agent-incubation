"""Migrated to arc.runtime.deploy.lambda_handler. Thin re-export shim.

Both public symbols and the internal `_FoundryLambdaHandler` /
`_FoundryStreamingHandler` classes are re-exported for backward compat —
the existing test suite directly tests the internal classes.
"""

from arc.runtime.deploy.lambda_handler import (
    _FoundryLambdaHandler,
    _FoundryStreamingHandler,
    make_handler,
    make_streaming_handler,
)

__all__ = [
    "make_handler",
    "make_streaming_handler",
    "_FoundryLambdaHandler",
    "_FoundryStreamingHandler",
]

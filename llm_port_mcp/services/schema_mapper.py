"""Maps MCP tool schemas into OpenAI-compatible function tool definitions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from llm_port_mcp.services.transport.base import MCPToolDescriptor


def to_openai_tool(descriptor: MCPToolDescriptor) -> dict[str, Any]:
    """Convert an ``MCPToolDescriptor`` to an OpenAI-compatible tool dict.

    Returns a dict shaped like::

        {
            "type": "function",
            "function": {
                "name": "mcp.prefix.tool_name",
                "description": "...",
                "parameters": { ... JSON Schema ... }
            }
        }
    """
    parameters = dict(descriptor.input_schema) if descriptor.input_schema else {}

    # Ensure the parameters block has a top-level type
    if parameters and "type" not in parameters:
        parameters["type"] = "object"

    return {
        "type": "function",
        "function": {
            "name": descriptor.qualified_name,
            "description": descriptor.description or descriptor.upstream_name,
            "parameters": parameters,
        },
    }


def compute_schema_hash(input_schema: dict[str, Any]) -> str:
    """Compute a stable SHA-256 hash of the tool's input schema.

    Used to detect when a tool's schema has changed between discoveries.
    """
    canonical = json.dumps(input_schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

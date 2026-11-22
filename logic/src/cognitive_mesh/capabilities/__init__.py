"""Typed registry of the external capabilities agents can use."""

from cognitive_mesh.capabilities.registry import (
    CAPABILITIES,
    AgentRole,
    Capability,
    CapabilityDeniedError,
    RiskLevel,
    authorize,
    capabilities_for,
    get_capability,
    read_tool_slugs,
    write_tool_slugs,
)

__all__ = [
    "CAPABILITIES",
    "AgentRole",
    "Capability",
    "CapabilityDeniedError",
    "RiskLevel",
    "authorize",
    "capabilities_for",
    "get_capability",
    "read_tool_slugs",
    "write_tool_slugs",
]

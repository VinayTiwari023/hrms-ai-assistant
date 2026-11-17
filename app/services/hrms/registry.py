"""Generic HRMS tool registry for role-aware model tool calling."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
import logging
from typing import Any

from app.core.exceptions import ResourceNotFoundError

logger = logging.getLogger(__name__)

ALL_ROLES: frozenset[str] = frozenset({"employee", "hr", "admin"})
HR_AND_ADMIN_ROLES: frozenset[str] = frozenset({"hr", "admin"})
ADMIN_ONLY_ROLES: frozenset[str] = frozenset({"admin"})


@dataclass(frozen=True)
class ToolDefinition:
    """Metadata, authorization specification, and handler for an individual AI tool."""

    name: str
    description: str
    parameters: dict[str, Any]
    allowed_roles: frozenset[str]
    handler: Callable[..., Coroutine[Any, Any, Any]]
    is_mutation: bool = False

    def is_authorized(self, role: str) -> bool:
        """Verify whether the caller's role is granted permission to invoke this tool."""
        role_clean = (role or "").strip().lower()
        if "*" in self.allowed_roles or "all" in self.allowed_roles:
            return True
        return role_clean in self.allowed_roles

    def to_ollama_tool(self) -> dict[str, Any]:
        """Convert definition into native Ollama/OpenAI function calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Central repository managing approved, role-filtered AI tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """Register a new tool definition."""
        self._tools[tool.name] = tool
        logger.debug("Registered HRMS tool '%s' (roles: %s)", tool.name, list(tool.allowed_roles))

    def get(self, name: str) -> ToolDefinition:
        """Retrieve tool definition by name.

        Raises:
            ResourceNotFoundError: If tool is not registered.
        """
        tool = self._tools.get(name)
        if not tool:
            raise ResourceNotFoundError(f"Tool '{name}' is not registered in the tool registry.")
        return tool

    def has_tool(self, name: str) -> bool:
        """Check whether a tool exists in the registry."""
        return name in self._tools

    def list_tools(self, role: str | None = None) -> list[ToolDefinition]:
        """List all tools, optionally filtered by the caller's authorized role."""
        if role is None:
            return list(self._tools.values())
        return [t for t in self._tools.values() if t.is_authorized(role)]

    def to_ollama_tools(self, role: str | None = None) -> list[dict[str, Any]]:
        """Export Ollama-compatible function calling schemas filtered by role."""
        return [t.to_ollama_tool() for t in self.list_tools(role=role)]

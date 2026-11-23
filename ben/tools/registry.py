"""Tool framework and registry for BEN — Phase 6.

Implements:
- class Tool: name, description, risk_level, async execute(args)
- class ToolRegistry: add(tool), get(name), list_all()
- registry singleton
"""

import re
from typing import Any, Callable, Dict, List, Optional


class Tool:
    """Base class for permission-controlled tools."""

    def __init__(
        self,
        name: str,
        description: str,
        risk_level: str = "read_only",
        requires_approval: bool = False,
        execute_fn: Optional[Callable[..., Any]] = None,
    ):
        self.name = name
        self.description = description
        self.risk_level = risk_level
        self.requires_approval = requires_approval
        self._execute_fn = execute_fn

    async def execute(self, args: Dict[str, Any]) -> Any:
        """Execute the tool with provided arguments."""
        if self._execute_fn:
            import inspect
            if inspect.iscoroutinefunction(self._execute_fn):
                return await self._execute_fn(args)
            return self._execute_fn(args)
        raise NotImplementedError("Subclasses must implement execute()")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
        }


class ToolRegistry:
    """Registry managing available tools."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        """Add tool to registry."""
        self._tools[tool.name] = tool
        self._tools[tool.name.lower()] = tool
        # snake_case alias
        snake = re.sub(r'(?<!^)(?=[A-Z])', '_', tool.name).lower()
        self._tools[snake] = tool
        # PascalCase alias
        pascal = "".join(part.capitalize() for part in tool.name.split("_"))
        self._tools[pascal] = tool

    def get(self, name: str) -> Optional[Tool]:
        """Return tool by name (case-insensitive)."""
        if not name:
            return None
        return self._tools.get(name) or self._tools.get(name.lower())

    def list_all(self) -> List[Dict[str, Any]]:
        """Return all tools."""
        unique = {t.name: t for t in self._tools.values()}
        return [t.to_dict() for t in unique.values()]


# Global registry singleton
registry = ToolRegistry()

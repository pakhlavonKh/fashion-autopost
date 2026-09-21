"""LLM provider interfaces and DTOs.

Per SDD §3.3 and SRS FR-2.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from adapters.base import RawProduct


@dataclass(frozen=True)
class SelectionResult:
    """Product selected by LLM along with generated marketing description."""
    external_id: str
    description: str


@runtime_checkable
class PromptLoader(Protocol):
    """Interface for dynamically loading the selection & copywriting prompt."""

    def load_prompt(self) -> str:
        """Load fresh prompt content from disk or other source."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """Interface for AI-driven product curation and description generation."""

    def select_products(
        self,
        candidates: list[RawProduct],
        prompt: str,
        max_items: int,
    ) -> list[SelectionResult]:
        """Curate candidate products according to prompt criteria and generate copy."""
        ...

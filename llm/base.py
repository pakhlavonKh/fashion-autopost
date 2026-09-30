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
    title: str | None = None


@dataclass
class HighlightSelectionResult:
    """Structured result of AI highlight selection."""
    highlight: str | None = None
    confidence: float = 1.0
    create_highlight: bool = False
    suggested_name: str | None = None

    @property
    def selected_highlight_name(self) -> str:
        """Returns the chosen existing highlight or suggested new highlight."""
        if self.highlight and self.highlight.strip():
            return self.highlight.strip()
        if self.suggested_name and self.suggested_name.strip():
            return self.suggested_name.strip()
        return "New Arrivals"


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

    def select_highlight(
        self,
        product: dict,
        existing_highlights: list[str],
    ) -> HighlightSelectionResult:
        """Select existing Highlight or suggest new Highlight for product."""
        ...

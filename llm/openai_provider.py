"""OpenAI implementation of the LLMProvider interface.

Per SDD §3.3 and SRS FR-2.
Enforces strict JSON schema parsing, hot prompt file reloading, and retry logic.
"""

import json
import logging
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field
from openai import OpenAI, OpenAIError

from adapters.base import RawProduct
from llm.base import LLMProvider, PromptLoader, SelectionResult

logger = logging.getLogger(__name__)


class FilePromptLoader:
    """Loads prompt from disk fresh on each execution (SRS FR-2.3)."""

    def __init__(self, prompt_path: str | Path) -> None:
        self.prompt_path = Path(prompt_path)

    def load_prompt(self) -> str:
        if not self.prompt_path.exists():
            logger.warning("Prompt file not found at %s, using fallback.", self.prompt_path)
            return (
                "Select the best fashion items for a high-end Telegram and Instagram audience. "
                "Write an elegant 1-2 sentence marketing description for each."
            )
        try:
            return self.prompt_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.error("Error reading prompt file %s: %s", self.prompt_path, exc)
            return "Curate stylish fashion items and write short elegant descriptions."


class _SelectedItemSchema(BaseModel):
    external_id: str = Field(description="The unique external_id of the selected product")
    title: str | None = Field(default=None, description="Refined, accurate product title translated into Russian")
    description: str = Field(description="Captivating marketing description in Russian for Telegram/Instagram")


class _SelectionResponseSchema(BaseModel):
    selected_products: list[_SelectedItemSchema] = Field(default_factory=list)


class OpenAIProvider:
    """Production OpenAI provider with JSON mode, retries, and fallback handling."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4.1",
        temperature: float = 0.7,
        max_retries: int = 1,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self._is_mock = not api_key or "mock" in api_key.lower()

    def select_products(
        self,
        candidates: list[RawProduct],
        prompt: str,
        max_items: int,
    ) -> list[SelectionResult]:
        """Send candidate products to OpenAI and return strictly parsed SelectionResults."""
        if not candidates:
            return []

        if self._is_mock:
            logger.info("OpenAIProvider running in mock mode: selecting up to %d items.", max_items)
            return self._mock_selection(candidates, max_items)

        client = OpenAI(api_key=self.api_key)

        # Prepare candidate payload for the LLM
        candidate_payload = [
            {
                "external_id": c.external_id,
                "brand": c.source,
                "title": c.title,
                "price": float(c.price),
                "currency": c.currency,
                "product_url": c.product_url,
            }
            for c in candidates
        ]

        system_instruction = (
            f"{prompt}\n\n"
            f"You may select at most {max_items} products.\n"
            "Both product name (title) and description MUST be written in Russian.\n"
            "You MUST respond ONLY with a valid JSON object matching this structure:\n"
            "{\n"
            '  "selected_products": [\n'
            '    {"external_id": "<id>", "title": "<translated Russian title>", "description": "<description in Russian>"}\n'
            "  ]\n"
            "}"
        )

        attempts = 0
        last_error: Exception | None = None

        while attempts <= self.max_retries:
            attempts += 1
            try:
                logger.info(
                    "Calling OpenAI (model=%s, attempt=%d/%d) with %d candidates",
                    self.model,
                    attempts,
                    self.max_retries + 1,
                    len(candidate_payload),
                )
                response = client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_instruction},
                        {
                            "role": "user",
                            "content": f"Here are the candidate products:\n{json.dumps(candidate_payload, indent=2)}",
                        },
                    ],
                )

                content = response.choices[0].message.content or "{}"
                parsed = self._parse_json_response(content, candidates)
                logger.info("OpenAI selection succeeded: %d items chosen", len(parsed))
                return parsed[:max_items]

            except (OpenAIError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "OpenAI selection attempt %d failed: %s",
                    attempts,
                    exc,
                )

        if last_error and any(err_token in str(last_error).lower() for err_token in ("insufficient_quota", "credit_balance_exhausted", "quota")):
            logger.warning(
                "OpenAI credit balance is exhausted ($0 balance on platform.openai.com). "
                "Falling back to template descriptions so publishing to Telegram completes. Error: %s",
                last_error,
            )
            return self._mock_selection(candidates, max_items)

        logger.error(
            "OpenAI selection exhausted all %d attempts. Skipping cycle gracefully per FR-2.6. Last error: %s",
            self.max_retries + 1,
            last_error,
        )
        return []

    def _parse_json_response(self, raw_json: str, candidates: list[RawProduct]) -> list[SelectionResult]:
        """Parse and validate JSON string against Pydantic schema and known candidate IDs."""
        data = json.loads(raw_json)
        validated = _SelectionResponseSchema.model_validate(data)
        
        valid_ids = {c.external_id for c in candidates}
        results: list[SelectionResult] = []

        for item in validated.selected_products:
            if item.external_id in valid_ids:
                results.append(
                    SelectionResult(
                        external_id=item.external_id,
                        description=item.description.strip(),
                        title=item.title.strip() if item.title else None,
                    )
                )
            else:
                logger.warning("LLM returned unknown external_id '%s', ignoring.", item.external_id)

        return results

    def _mock_selection(self, candidates: list[RawProduct], max_items: int) -> list[SelectionResult]:
        """Deterministic mock selector for local offline testing."""
        selected: list[SelectionResult] = []
        for product in candidates[:max_items]:
            desc = (
                f"Элегантная модель от {product.source.upper()}. "
                "Безупречный силуэт, премиальные материалы и идеальная посадка для современного гардероба."
            )
            selected.append(SelectionResult(external_id=product.external_id, description=desc, title=product.title))
        return selected

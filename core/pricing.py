"""Pricing engine and currency conversion protocols.

Per SDD §3.4 and SRS FR-3 & §10.3 (Open Question 3: Currency conversion logic).
Computes: price_final = convert(original_price, currency -> target_currency) + markup.
"""

from decimal import Decimal, ROUND_HALF_UP
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class FxConverter(Protocol):
    """Interface for foreign exchange conversion between currencies."""

    def convert(self, amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
        """Convert amount from from_currency into to_currency."""
        ...


class FixedRateConverter:
    """Fixed-rate currency converter using configured exchange rate or table."""

    def __init__(self, fixed_rate: Decimal = Decimal("1.0"), rates: dict[str, Decimal] | None = None) -> None:
        self.fixed_rate = fixed_rate
        # Rates dictionary maps CURRENCY -> USD multiplier (e.g., {"EUR": Decimal("1.08")})
        self.rates = rates or {}

    def convert(self, amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
        from_curr = from_currency.upper().strip()
        to_curr = to_currency.upper().strip()

        if from_curr == to_curr:
            return amount

        # If rates map is provided, use cross-rate calculation
        if from_curr in self.rates and to_curr in self.rates:
            from_rate = self.rates[from_curr]
            to_rate = self.rates[to_curr]
            converted = (amount * from_rate) / to_rate
            return converted

        # If from_curr is in rates and target is assumed USD
        if from_curr in self.rates and to_curr == "USD":
            return amount * self.rates[from_curr]

        # Fallback to general fixed_rate multiplier
        logger.info(
            "Using fallback fixed rate %s for conversion %s -> %s",
            self.fixed_rate,
            from_curr,
            to_curr,
        )
        return amount * self.fixed_rate


class DynamicRateConverter:
    """Dynamic rate converter supporting live API feeds with cached fallback."""

    def __init__(
        self,
        cached_rates: dict[str, Decimal] | None = None,
        fallback_rate: Decimal = Decimal("1.0"),
    ) -> None:
        self.rates = cached_rates or {"EUR": Decimal("1.08"), "USD": Decimal("1.00")}
        self.fallback_rate = fallback_rate

    def update_rate(self, currency: str, rate_to_target: Decimal) -> None:
        self.rates[currency.upper()] = rate_to_target

    def convert(self, amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
        from_curr = from_currency.upper().strip()
        to_curr = to_currency.upper().strip()

        if from_curr == to_curr:
            return amount

        if from_curr in self.rates and to_curr in self.rates:
            return (amount * self.rates[from_curr]) / self.rates[to_curr]

        logger.warning(
            "Dynamic rate unavailable for %s -> %s, using fallback rate %s",
            from_curr,
            to_curr,
            self.fallback_rate,
        )
        return amount * self.fallback_rate


def calculate_final_price(
    original_price: Decimal,
    currency: str,
    markup: Decimal,
    target_currency: str,
    fx: FxConverter,
) -> Decimal:
    """Calculate final consumer sale price with currency conversion and markup.
    
    Formula: price_final = convert(original_price, currency -> target_currency) + markup
    Rounds result to 2 decimal places with ROUND_HALF_UP.
    """
    if original_price < Decimal("0"):
        raise ValueError(f"original_price cannot be negative: {original_price}")
    if markup < Decimal("0"):
        raise ValueError(f"markup cannot be negative: {markup}")

    converted_price = fx.convert(original_price, currency, target_currency)
    final_price = converted_price + markup
    return final_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

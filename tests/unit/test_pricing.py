"""Unit tests for pricing engine and FX converters.

Per SDD §3.4 and §9.
"""

from decimal import Decimal
import pytest

from core.pricing import (
    DynamicRateConverter,
    FixedRateConverter,
    calculate_final_price,
)


def test_calculate_final_price_same_currency() -> None:
    """When product and target currencies match, no FX conversion is applied."""
    fx = FixedRateConverter(fixed_rate=Decimal("1.0"))
    final = calculate_final_price(
        original_price=Decimal("50.00"),
        currency="USD",
        markup=Decimal("15.00"),
        target_currency="USD",
        fx=fx,
    )
    assert final == Decimal("65.00")


def test_calculate_final_price_fixed_rate_conversion() -> None:
    """Validate formula: price_final = (original_price * fx_rate) + markup."""
    # 100.00 EUR * 1.08 = 108.00 USD + 15.00 markup = 123.00 USD
    fx = FixedRateConverter(fixed_rate=Decimal("1.08"))
    final = calculate_final_price(
        original_price=Decimal("100.00"),
        currency="EUR",
        markup=Decimal("15.00"),
        target_currency="USD",
        fx=fx,
    )
    assert final == Decimal("123.00")


def test_calculate_final_price_rounding() -> None:
    """Ensure fractional cents are properly rounded using standard round half up."""
    # 49.90 * 1.08 = 53.892 + 15.00 = 68.892 -> 68.89
    fx = FixedRateConverter(fixed_rate=Decimal("1.08"))
    final = calculate_final_price(
        original_price=Decimal("49.90"),
        currency="EUR",
        markup=Decimal("15.00"),
        target_currency="USD",
        fx=fx,
    )
    assert final == Decimal("68.89")


def test_fixed_rate_converter_cross_rates() -> None:
    """Test conversion using cross-rate lookup table."""
    rates = {
        "EUR": Decimal("1.08"),
        "TRY": Decimal("0.027"),
        "USD": Decimal("1.00"),
    }
    fx = FixedRateConverter(rates=rates)
    
    # 1000 TRY in USD = 1000 * 0.027 = 27 USD + 10 markup = 37 USD
    final = calculate_final_price(
        original_price=Decimal("1000.00"),
        currency="TRY",
        markup=Decimal("10.00"),
        target_currency="USD",
        fx=fx,
    )
    assert final == Decimal("37.00")


def test_dynamic_rate_converter() -> None:
    """Test dynamic rate converter with live update."""
    dyn_fx = DynamicRateConverter(cached_rates={"EUR": Decimal("1.05"), "USD": Decimal("1.00")})
    assert dyn_fx.convert(Decimal("100"), "EUR", "USD") == Decimal("105.00")

    # Update exchange rate dynamically
    dyn_fx.update_rate("EUR", Decimal("1.10"))
    assert dyn_fx.convert(Decimal("100"), "EUR", "USD") == Decimal("110.00")


def test_calculate_final_price_negative_validation() -> None:
    """Negative prices or negative markups must raise ValueError."""
    fx = FixedRateConverter()
    with pytest.raises(ValueError, match="original_price cannot be negative"):
        calculate_final_price(Decimal("-10.00"), "USD", Decimal("5.00"), "USD", fx)

    with pytest.raises(ValueError, match="markup cannot be negative"):
        calculate_final_price(Decimal("50.00"), "USD", Decimal("-5.00"), "USD", fx)

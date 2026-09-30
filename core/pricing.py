"""Pricing engine and currency conversion protocols.

Per SDD §3.4 and SRS FR-3 & §10.3 (Open Question 3: Currency conversion logic).
Computes: price_final = convert(original_price, currency -> target_currency) + markup.
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import json
import logging
from pathlib import Path
import threading
from typing import Protocol, runtime_checkable

import httpx

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

        # If fixed_rate was customized (not default 1.0) and rates is empty, use fixed_rate
        if self.fixed_rate != Decimal("1.0") and not self.rates:
            return amount * self.fixed_rate

        # If from_curr or to_curr is missing from fixed table, attempt dynamic online lookup
        try:
            dyn = DynamicRateConverter(cached_rates=self.rates, fallback_rate=self.fixed_rate)
            return dyn.convert(amount, from_currency, to_currency)
        except Exception:
            pass

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

    CACHE_FILE = Path("data/fx_rates_cache.json")

    def __init__(
        self,
        cached_rates: dict[str, Decimal] | None = None,
        fallback_rate: Decimal = Decimal("1.0"),
        cache_ttl_seconds: int = 3600,
    ) -> None:
        self.rates: dict[str, Decimal] = {
            "USD": Decimal("1.00"),
            "EUR": Decimal("1.08"),
            "GBP": Decimal("1.29"),
            "TRY": Decimal("0.029"),
        }
        self.last_fetched: datetime | None = None
        self._custom_seed = bool(cached_rates)
        if cached_rates:
            for k, v in cached_rates.items():
                self.rates[k.upper().strip()] = Decimal(str(v))
            self.last_fetched = datetime.now(timezone.utc)
        self.fallback_rate = fallback_rate
        self.cache_ttl_seconds = cache_ttl_seconds
        self._lock = threading.Lock()
        if not cached_rates:
            self._load_disk_cache()

    def update_rate(self, currency: str, rate_to_target: Decimal) -> None:
        self.rates[currency.upper().strip()] = Decimal(str(rate_to_target))

    def convert(self, amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
        from_curr = from_currency.upper().strip()
        to_curr = to_currency.upper().strip()

        if from_curr == to_curr:
            return amount

        # If rates are missing or cache is stale (and not explicitly customized in-memory), refresh rates online
        needs_refresh = (from_curr not in self.rates or to_curr not in self.rates) or (
            not self._custom_seed and self._is_cache_stale()
        )
        if needs_refresh:
            self._refresh_rates_online(needed_currencies=[from_curr, to_curr])

        if from_curr in self.rates and to_curr in self.rates:
            return (amount * self.rates[from_curr]) / self.rates[to_curr]

        logger.warning(
            "Dynamic rate unavailable for %s -> %s, using fallback rate %s",
            from_curr,
            to_curr,
            self.fallback_rate,
        )
        return amount * self.fallback_rate

    def _is_cache_stale(self) -> bool:
        if self.last_fetched is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self.last_fetched).total_seconds()
        return elapsed > self.cache_ttl_seconds

    def _refresh_rates_online(self, needed_currencies: list[str] | None = None) -> bool:
        with self._lock:
            # Recheck after acquiring lock
            if needed_currencies and all(c in self.rates for c in needed_currencies) and not self._is_cache_stale():
                return True
            try:
                rates_dict = self._fetch_open_er_api()
                if not rates_dict:
                    rates_dict = self._fetch_exchangerate_api()
                if rates_dict:
                    for curr, val in rates_dict.items():
                        try:
                            num = Decimal(str(val))
                            if num > Decimal("0"):
                                # Open ER API returns units per USD; USD multiplier = 1 / num
                                self.rates[curr.upper()] = (Decimal("1.0") / num).quantize(Decimal("0.00000001"))
                        except Exception:
                            continue
                    self.rates["USD"] = Decimal("1.0")
                    self.last_fetched = datetime.now(timezone.utc)
                    self._save_disk_cache()
                    logger.info("Successfully fetched live FX rates for %d currencies.", len(rates_dict))
                    return True
            except Exception as exc:
                logger.warning("Could not fetch online dynamic FX rates: %s", exc)
        return False

    def _fetch_open_er_api(self) -> dict[str, float] | None:
        url = "https://open.er-api.com/v6/latest/USD"
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("result") == "success" and isinstance(data.get("rates"), dict):
                    return data["rates"]
        return None

    def _fetch_exchangerate_api(self) -> dict[str, float] | None:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data.get("rates"), dict):
                    return data["rates"]
        return None

    def _load_disk_cache(self) -> None:
        if not self.CACHE_FILE.is_file():
            return
        try:
            content = json.loads(self.CACHE_FILE.read_text(encoding="utf-8"))
            rates = content.get("rates", {})
            for k, v in rates.items():
                self.rates[k.upper()] = Decimal(str(v))
            ts = content.get("timestamp")
            if ts:
                self.last_fetched = datetime.fromisoformat(ts)
        except Exception as exc:
            logger.debug("Failed to read FX disk cache: %s", exc)

    def _save_disk_cache(self) -> None:
        try:
            self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            serializable = {
                "timestamp": self.last_fetched.isoformat() if self.last_fetched else None,
                "rates": {k: str(v) for k, v in self.rates.items()},
            }
            self.CACHE_FILE.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("Failed to write FX disk cache: %s", exc)


def whole_price(amount: Decimal) -> Decimal:
    """Drop cents by rounding down to a whole currency unit.

    42.39 becomes 42. The store price itself is not changed.
    """
    return amount.to_integral_value(rounding=ROUND_DOWN)


def calculate_final_price(
    original_price: Decimal,
    currency: str,
    markup: Decimal,
    target_currency: str,
    fx: FxConverter,
) -> Decimal:
    """Calculate final consumer sale price with currency conversion and markup.

    Formula: price_final = convert(original_price, currency -> target_currency) + markup.
    Cents are rounded down, so the posted price is a whole number.
    """
    if original_price < Decimal("0"):
        raise ValueError(f"original_price cannot be negative: {original_price}")
    if markup < Decimal("0"):
        raise ValueError(f"markup cannot be negative: {markup}")

    converted_price = fx.convert(original_price, currency, target_currency)
    return whole_price(converted_price + markup)


def source_price_usd(original_price: Decimal, currency: str, fx: FxConverter) -> Decimal:
    """Convert the store price into whole USD before any markup is added.

    Cents are rounded down: 59.60 USD becomes 59.
    """
    if original_price < Decimal("0"):
        raise ValueError(f"original_price cannot be negative: {original_price}")
    converted = fx.convert(original_price, currency, "USD")
    return converted.to_integral_value(rounding=ROUND_DOWN)

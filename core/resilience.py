"""Resilience and retry utilities.

Per SDD §3.9 and SRS FR-7.4 & NFR-3.
Provides exponential backoff decorator for transient network and API failures.
"""

from functools import wraps
import logging
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """Decorator providing exponential backoff retries on transient errors."""

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 0
            delay = base_delay

            while attempt < max_attempts:
                attempt += 1
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt >= max_attempts:
                        logger.error(
                            "Function %s failed after %d attempts. Raising final error: %s",
                            func.__name__,
                            attempt,
                            exc,
                        )
                        raise

                    logger.warning(
                        "Function %s raised %s (attempt %d/%d). Retrying in %.2fs...",
                        func.__name__,
                        exc,
                        attempt,
                        max_attempts,
                        delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * backoff_factor, max_delay)

        return wrapper  # type: ignore

    return decorator

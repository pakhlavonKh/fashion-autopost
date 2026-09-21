"""Deduplication service.

Per SDD §3.2 and SRS FR-1.4 & FR-6.2.
Filters candidate products against previously published items stored in the repository.
"""

import logging
from adapters.base import RawProduct
from storage.repository import ProductRepository

logger = logging.getLogger(__name__)


def filter_unseen(products: list[RawProduct], repo: ProductRepository) -> list[RawProduct]:
    """Drop any product whose external_id already has status='published' in the database.
    
    Pure domain function that decouples deduplication business logic from storage I/O.
    """
    published_ids = repo.get_published_ids()
    unseen: list[RawProduct] = []

    for product in products:
        if product.external_id in published_ids:
            logger.debug(
                "Discarding previously published product: external_id=%s (source=%s)",
                product.external_id,
                product.source,
            )
        else:
            unseen.append(product)

    logger.info(
        "filter_unseen: %d candidates in, %d discarded as already published, %d remaining",
        len(products),
        len(products) - len(unseen),
        len(unseen),
    )
    return unseen

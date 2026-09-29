"""
Test: publish the latest product from the DB to BOTH Telegram and Instagram,
exactly as the pipeline does.

Usage:
    d:\\projects\\fashion-autopost\\.venv\\Scripts\\python.exe test_dual_publish.py
"""

from __future__ import annotations
import os
import sys
import logging
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# force UTF-8 output on Windows
sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("dual_test")

# ── grab a real product from the DB ─────────────────────────────────────────
from storage.repository import SqlAlchemyProductRepository
from storage.models import ProductRecord
from sqlalchemy import select, desc

DB_URL = os.getenv("DB_URL", "sqlite:///./data/app.db")
repo = SqlAlchemyProductRepository(DB_URL)

with repo._get_session() as session:
    rec = session.scalars(
        select(ProductRecord)
        .where(ProductRecord.photo_url.isnot(None))
        .where(ProductRecord.price_final.isnot(None))
        .order_by(desc(ProductRecord.created_at))
        .limit(1)
    ).first()
    if rec is None:
        sys.exit("No products with photos found in DB. Run a pipeline cycle first.")

    # snapshot all fields before session closes
    external_id    = rec.external_id
    source         = rec.source
    title          = rec.title
    price_original = rec.price_original
    currency_orig  = rec.currency_original
    price_final    = rec.price_final
    photo_url      = rec.photo_url
    product_url    = rec.product_url
    description    = rec.description_gpt or ""

logger.info("Using product: %s | %s | %s", external_id, source, title)
logger.info("Photo: %s", photo_url)

# ── build a ComposedPost ────────────────────────────────────────────────────
from adapters.base import RawProduct
from core.composer import compose_post

product = RawProduct(
    external_id=external_id,
    source=source,
    title=title,
    price=price_original,
    currency=currency_orig,
    photo_url=photo_url,
    product_url=product_url or "",
    in_stock=True,
)

post = compose_post(
    product=product,
    description=description,
    price=price_final,
    target_currency="USD",
    include_link=bool(product_url),
)

# ── collect local gallery images if available ────────────────────────────────
from core.image_downloader import is_material_or_color_swatch
images_dir = Path("data/images")
raw_gallery = sorted(images_dir.glob(f"{external_id}_*.webp")) + \
              sorted(images_dir.glob(f"{external_id}_*.jpg"))

gallery = [p for p in raw_gallery if not is_material_or_color_swatch(p)]

if gallery:
    photo_urls = [str(p) for p in gallery[:10]]
    logger.info("Found %d local gallery images for %s (swatches filtered out)", len(photo_urls), external_id)
else:
    photo_urls = [photo_url] if photo_url else []
    logger.info("No local gallery found, using original URL: %s", photo_url)

# patch photo_urls into the post
post = type(post)(
    photo_url=photo_urls[0] if photo_urls else photo_url,
    photo_urls=photo_urls,
    text=post.text,
    price=post.price,
    currency=post.currency,
    product_url=post.product_url,
    title=post.title,
    source=post.source,
)

# ── build publishers ─────────────────────────────────────────────────────────
from config.app_config import AppConfig
config = AppConfig.load("config.yaml")

# Telegram
from publishers.telegram_publisher import TelegramPublisher
tg_token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
tg_channel = os.getenv("TELEGRAM_CHANNEL_ID", "")
if not tg_token or not tg_channel:
    sys.exit("TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not set in .env")

tg_publisher = TelegramPublisher(
    bot_token=tg_token,
    channel_id=tg_channel,
    bio_footer=getattr(config.telegram, "bio_footer", ""),
)

# Instagram
from publishers.instagram_publisher import InstagramPublisher
from publishers.image_hosting import LitterboxImageHost

ig_token      = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
ig_account_id = os.getenv("INSTAGRAM_ACCOUNT_ID", "")
ig_username   = os.getenv("INSTAGRAM_USERNAME", "")
if not ig_token or not ig_account_id:
    sys.exit("INSTAGRAM_ACCESS_TOKEN or INSTAGRAM_ACCOUNT_ID not set in .env")

ig_publisher = InstagramPublisher(
    access_token=ig_token,
    account_id=ig_account_id,
    image_host=LitterboxImageHost(),
    username=ig_username,
    caption_footer=getattr(config.instagram, "caption_footer", ""),
)

# ── publish ──────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"  Product : {title}")
print(f"  Source  : {source} | ID: {external_id}")
print(f"  Price   : ${float(price_final):.2f}")
print(f"  Photos  : {len(photo_urls)}")
print(f"{'='*60}\n")

# --- Telegram ---
logger.info("Publishing to Telegram channel %s ...", tg_channel)
tg_result = tg_publisher.publish(post)
if tg_result.success:
    print(f"[TELEGRAM] SUCCESS — post ID: {tg_result.platform_post_id}")
else:
    print(f"[TELEGRAM] FAILED  — {tg_result.error}")

# --- Instagram ---
logger.info("Publishing to Instagram @%s (account %s) ...", ig_username, ig_account_id)
ig_result = ig_publisher.publish(post)
if ig_result.success:
    print(f"[INSTAGRAM] SUCCESS — post ID: {ig_result.platform_post_id}")
    print(f"            https://www.instagram.com/p/{ig_result.platform_post_id}/")
else:
    print(f"[INSTAGRAM] FAILED  — {ig_result.error}")

# ── summary ──────────────────────────────────────────────────────────────────
print()
ok = tg_result.success and ig_result.success
print("BOTH PUBLISHED OK" if ok else "ONE OR MORE FAILED")
sys.exit(0 if ok else 1)

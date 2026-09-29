"""
Quick smoke-test: publishes ONE real post to Instagram via the Graph API.

Usage:
    .venv\\Scripts\\python.exe test_instagram_live.py

Requirements:
- INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID must be set in .env
- Pillow must be installed (it's in requirements.txt)
"""

from __future__ import annotations

import os
import sys
import tempfile
import logging
from decimal import Decimal
from pathlib import Path

# ── load .env ───────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ig_test")

# ── check credentials ───────────────────────────────────────────────────────
ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
ACCOUNT_ID   = os.getenv("INSTAGRAM_ACCOUNT_ID", "")
USERNAME     = os.getenv("INSTAGRAM_USERNAME", "")

if not ACCESS_TOKEN or "your_" in ACCESS_TOKEN:
    sys.exit("❌  INSTAGRAM_ACCESS_TOKEN not set in .env")
if not ACCOUNT_ID or "your_" in ACCOUNT_ID:
    sys.exit("❌  INSTAGRAM_ACCOUNT_ID not set in .env")

logger.info("Credentials OK — account_id=%s, username=%s", ACCOUNT_ID, USERNAME)

# ── build a minimal ComposedPost ────────────────────────────────────────────
from adapters.base import RawProduct
from core.composer import compose_post

product = RawProduct(
    external_id="ig-test-001",
    source="zara",
    title="Test Post — Please Ignore",
    price=Decimal("49.99"),
    currency="EUR",
    photo_url="https://static.zara.net/assets/public/e0bc/9d8c/e0bc9d8ce3f64cf2a1b4b4e0e0e1b2c3.jpg",
    product_url="https://www.zara.com/",
    in_stock=True,
)

post = compose_post(
    product=product,
    description="Это тестовая публикация. Не обращайте внимания.",
    price=Decimal("53.99"),
    target_currency="USD",
    include_link=False,
)

# ── create a real 1080x1350 test image ─────────────────────────────────────
from PIL import Image, ImageDraw, ImageFont

logger.info("Creating test image 1080x1350 ...")
img = Image.new("RGB", (1080, 1350), color=(20, 20, 40))
draw = ImageDraw.Draw(img)
for y in range(1350):
    r = int(20 + (y / 1350) * 60)
    g = int(20 + (y / 1350) * 10)
    b = int(40 + (y / 1350) * 80)
    draw.line([(0, y), (1080, y)], fill=(r, g, b))

draw.rectangle([80, 540, 1000, 810], fill=(255, 255, 255), outline=(200, 200, 220), width=2)
try:
    font = ImageFont.truetype("arial.ttf", 64)
    small = ImageFont.truetype("arial.ttf", 36)
except Exception:
    font = ImageFont.load_default()
    small = font

draw.text((540, 620), "Instagram Test", fill=(30, 30, 30), font=font, anchor="mm")
draw.text((540, 720), "fashion-autopost · please ignore", fill=(80, 80, 100), font=small, anchor="mm")
draw.text((540, 780), "@mukhsinius", fill=(60, 60, 150), font=small, anchor="mm")

tmp_dir = Path(tempfile.mkdtemp(prefix="ig_test_"))
img_path = tmp_dir / "test_post.jpg"
img.save(img_path, "JPEG", quality=90)
logger.info("Test image saved: %s", img_path)

# patch the post's photo_url to our local file
post = type(post)(
    photo_url=str(img_path),
    photo_urls=[str(img_path)],
    text=post.text,
    price=post.price,
    currency=post.currency,
    product_url=post.product_url,
    title=post.title,
    source=post.source,
)

# ── publish ──────────────────────────────────────────────────────────────────
import httpx as _httpx
from publishers.instagram_publisher import InstagramPublisher
from publishers.image_hosting import ImageHostingService
from config.app_config import DEFAULT_INSTAGRAM_CAPTION_FOOTER


class CatboxImageHost:
    """Upload to catbox.moe (permanent, no expiry) — fallback when Litterbox is down."""
    UPLOAD_URL = "https://catbox.moe/user/api.php"

    def ensure_public_url(self, photo_url_or_path: str) -> str:
        from pathlib import Path as _Path
        local_path = _Path(photo_url_or_path)
        if not local_path.is_file():
            if photo_url_or_path.startswith("https://"):
                return photo_url_or_path
            raise ValueError(f"Not a local file or HTTPS URL: {photo_url_or_path}")
        with local_path.open("rb") as fh:
            files = {"fileToUpload": (local_path.name, fh, "image/jpeg")}
            data = {"reqtype": "fileupload"}
            with _httpx.Client(timeout=60.0) as client:
                resp = client.post(self.UPLOAD_URL, data=data, files=files)
                resp.raise_for_status()
        url = resp.text.strip()
        if not url.startswith("https://"):
            raise RuntimeError(f"catbox.moe did not return a URL: {url[:200]}")
        logger.info("Uploaded test image to %s", url)
        return url


publisher = InstagramPublisher(
    access_token=ACCESS_TOKEN,
    account_id=ACCOUNT_ID,
    image_host=CatboxImageHost(),
    username=USERNAME,
    caption_footer=DEFAULT_INSTAGRAM_CAPTION_FOOTER,
)

logger.info("Caption preview:\n%s", publisher.format_caption(post))
logger.info("Uploading image to catbox.moe and calling Graph API ...")

result = publisher.publish(post)

if result.success:
    print("SUCCESS - Instagram post ID: " + result.platform_post_id)
    print("https://www.instagram.com/p/" + result.platform_post_id + "/")
else:
    print("FAILED - " + str(result.error))
    sys.exit(1)


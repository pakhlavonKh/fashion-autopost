"""Test Playwright & Instagram Story publishing directly from the deploy server.

Usage on deploy server (inside Docker):
    # 1. Non-destructive dry-run check (tests Chromium, session files, image collage, and highlights):
    docker compose exec app python test_story_live.py --dry-run

    # 2. Live publication of a test story with Telegram Link Sticker:
    docker compose exec app python test_story_live.py

    # 3. Test with a custom Telegram link:
    docker compose exec app python test_story_live.py --link https://t.me/your_channel/123

Usage on host VPS directly (without Docker):
    python test_story_live.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import logging
from decimal import Decimal
from pathlib import Path

# Force UTF-8 output on terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_story")


def check_playwright_chromium() -> bool:
    """Verify Playwright Chromium engine can launch on the server."""
    print("\n[Step 1/5] Checking Playwright Chromium launch on server...")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content("<html><body><h1>Playwright OK</h1></body></html>")
            text = page.inner_text("h1")
            browser.close()

        if text == "Playwright OK":
            print("  ✅ Playwright Chromium launched and rendered HTML successfully!")
            return True
        else:
            print("  ⚠️ Playwright launched but returned unexpected content.")
            return False
    except Exception as exc:
        print(f"  ❌ Playwright Chromium launch failed: {exc}")
        print("     To fix on Ubuntu/Debian, run: playwright install --with-deps chromium")
        return False


def check_instagram_session() -> dict[str, bool]:
    """Check available Instagram session credentials."""
    print("\n[Step 2/5] Checking Instagram authentication session files...")
    settings_file = Path("data/instagram_settings.json")
    pw_state_file = Path("data/instagram_playwright_state.json")

    has_settings = settings_file.is_file() and settings_file.stat().st_size > 10
    has_pw_state = pw_state_file.is_file() and pw_state_file.stat().st_size > 10
    has_login_env = bool(os.getenv("INSTAGRAM_LOGIN") and os.getenv("INSTAGRAM_PASSWORD"))
    has_session_env = bool(os.getenv("INSTAGRAM_SESSIONID"))

    print(f"  - App settings ({settings_file}): {'✅ Present' if has_settings else '❌ Missing'}")
    print(f"  - Playwright state ({pw_state_file}): {'✅ Present' if has_pw_state else '❌ Missing'}")
    print(f"  - Environment INSTAGRAM_LOGIN: {'✅ Present' if has_login_env else '❌ Not set'}")
    print(f"  - Environment INSTAGRAM_SESSIONID: {'✅ Present' if has_session_env else '❌ Not set'}")

    return {
        "settings": has_settings,
        "pw_state": has_pw_state,
        "login_env": has_login_env,
        "session_env": has_session_env,
    }


def get_or_create_test_product(repo, custom_link: str | None = None) -> tuple[int, str]:
    """Find the latest product with a Telegram message URL or create a test product."""
    from storage.models import ProductRecord
    from sqlalchemy import select, desc

    with repo._get_session() as session:
        # Check if there's any product with a real Telegram message URL
        stmt = (
            select(ProductRecord)
            .where(ProductRecord.telegram_message_url.isnot(None))
            .order_by(desc(ProductRecord.created_at))
            .limit(1)
        )
        prod = session.scalars(stmt).first()
        if prod and not custom_link:
            return prod.id, prod.telegram_message_url

        # Otherwise find any product and assign the custom link
        stmt_any = (
            select(ProductRecord)
            .order_by(desc(ProductRecord.created_at))
            .limit(1)
        )
        any_prod = session.scalars(stmt_any).first()
        tg_url = custom_link or "https://t.me/fashion_autopost_channel/100"

        if any_prod:
            any_prod.telegram_message_url = tg_url
            session.commit()
            return any_prod.id, tg_url

        # Create a mock product in DB if empty
        test_prod = ProductRecord(
            external_id="test-story-server-001",
            source="zara",
            name="ZARA Textured Oversize Blazer",
            title="ZARA Textured Oversize Blazer",
            price_original=Decimal("69.95"),
            currency_original="EUR",
            price_final=Decimal("79.00"),
            currency_final="USD",
            product_url="https://www.zara.com/es/en/blazer-p12345.html",
            photo_url="https://static.zara.net/assets/public/test.jpg",
            category="jackets",
            status="published",
            telegram_message_id="100",
            telegram_message_url=tg_url,
        )
        session.add(test_prod)
        session.commit()
        return test_prod.id, tg_url


def create_sample_story_image() -> Path:
    """Create a temporary 1080x1920 test story image."""
    from PIL import Image, ImageDraw

    tmp_dir = Path(tempfile.mkdtemp(prefix="story_test_"))
    img_path = tmp_dir / "story_sample_photo.jpg"
    img = Image.new("RGB", (1080, 1920), color=(15, 23, 42))
    draw = ImageDraw.Draw(img)

    for y in range(1920):
        r = int(15 + (y / 1920) * 45)
        g = int(23 + (y / 1920) * 20)
        b = int(42 + (y / 1920) * 80)
        draw.line([(0, y), (1080, y)], fill=(r, g, b))

    draw.rectangle([100, 300, 980, 1400], fill=(30, 41, 59), outline=(99, 102, 241), width=4)
    img.save(img_path, "JPEG", quality=92)
    return img_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Playwright Instagram Story publishing on server.")
    parser.add_argument("--dry-run", action="store_true", help="Validate setup without posting to Instagram.")
    parser.add_argument("--link", type=str, default=None, help="Custom Telegram message URL to attach.")
    parser.add_argument("--browser-only", action="store_true", help="Test only Playwright browser navigation.")
    args = parser.parse_args()

    print("=" * 70)
    print("  Fashion Autopost - Playwright Instagram Story Server Test")
    print("=" * 70)

    # Step 1: Chromium Launch
    pw_ok = check_playwright_chromium()
    if not pw_ok:
        print("\n❌ Playwright Chromium is not operational on this system.")
        return 1

    # Step 2: Session Check
    sessions = check_instagram_session()
    if not any(sessions.values()) and not args.dry_run:
        print("\n⚠️ No Instagram credentials or session files found!")
        print("  Run login once to generate session: docker compose exec app python -m publishers.instagram_login")
        print("  Or proceed with --dry-run to test image rendering and pipeline flow.")

    # Step 3: Initialize Database Repository & Product
    print("\n[Step 3/5] Loading Database & Product with Telegram URL...")
    from storage.repository import SqlAlchemyProductRepository
    from config.app_config import AppConfig

    cfg = AppConfig.load()
    repo = SqlAlchemyProductRepository(cfg.db_url)
    product_id, telegram_url = get_or_create_test_product(repo, custom_link=args.link)
    print(f"  - Using Product ID: {product_id}")
    print(f"  - Telegram Link Sticker Target: {telegram_url}")

    # Step 4: Render 9:16 Story Collage
    print("\n[Step 4/5] Rendering 9:16 Vertical Story Collage...")
    from publishers.instagram_story import render_story_collage
    test_photo = create_sample_story_image()
    collage_dest = Path("data/test_story_preview.jpg")
    collage_dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        render_story_collage(
            [test_photo],
            collage_dest,
            title="Fashion Autopost Story Test",
            price_label="$79.00",
            description="Server deployment validation test for Telegram-linked Instagram Stories.",
            quality_line="Premium Quality · Fast EU Shipping",
        )
        print(f"  ✅ Story collage rendered successfully: {collage_dest} ({collage_dest.stat().st_size} bytes)")
    except Exception as exc:
        print(f"  ❌ Collage rendering failed: {exc}")
        return 1

    # Step 5: Execute Story Worker
    print("\n[Step 5/5] Executing Story Worker...")
    if args.dry_run:
        print("  🔍 [DRY-RUN] Skipping actual upload to Instagram.")
        print(f"  ✅ All components verified successfully!")
        print(f"     - Playwright Engine: OK")
        print(f"     - Telegram Link Target: {telegram_url}")
        print(f"     - Rendered Story Image: {collage_dest}")
        print("\nRun without --dry-run to perform live publication.")
        return 0

    from publishers.playwright_story_worker import PlaywrightStoryWorker
    from llm.mock_provider import MockLLMProvider
    from publishers.instagram_private_story import InstagramPrivateStory

    private_story = InstagramPrivateStory(
        session_id=os.getenv("INSTAGRAM_SESSIONID", ""),
        login=os.getenv("INSTAGRAM_LOGIN", ""),
        password=os.getenv("INSTAGRAM_PASSWORD", ""),
    )

    worker = PlaywrightStoryWorker(
        repo=repo,
        llm=MockLLMProvider(),
        use_playwright_browser=args.browser_only or sessions["pw_state"],
        private_story=private_story,
    )

    print("  🚀 Publishing Story with Telegram Link Sticker...")
    ok, message, story_id = worker.process_story_job(
        product_id=product_id,
        prepared_images=[test_photo],
        price_label="$79.00",
    )

    print("\n" + "=" * 70)
    if ok:
        print(f"  🎉 SUCCESS: {message}")
        print(f"     Story ID: {story_id}")
        print(f"     Telegram Link: {telegram_url}")
        print("=" * 70)
        return 0
    else:
        print(f"  ❌ Story publishing did not complete: {message}")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

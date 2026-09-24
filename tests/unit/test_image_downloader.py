"""Unit tests for ImageDownloader and local image publishing.

Per SRS FR-5 and SDD §3.6.
"""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from core.composer import ComposedPost
from core.image_downloader import ImageDownloader
from decimal import Decimal
from publishers.telegram_publisher import TelegramPublisher


def test_image_downloader_local_file_passthrough() -> None:
    """Verify local files that exist are returned immediately without network call."""
    with tempfile.NamedTemporaryFile("wb", suffix=".jpg", delete=False) as f:
        f.write(b"fake image content")
        local_path = Path(f.name)

    try:
        downloader = ImageDownloader(dest_dir="data/images")
        result = downloader.download(str(local_path), external_id="test-1")
        assert result == local_path
    finally:
        if local_path.exists():
            local_path.unlink()


def test_image_downloader_http_success() -> None:
    """Verify remote HTTP image download and saving to disk."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        downloader = ImageDownloader(dest_dir=tmp_dir)

        fake_bytes = b"fake-jpeg-image-bytes-1234567890" * 20

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.content = fake_bytes
            mock_resp.raise_for_status = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            path = downloader.download("https://example.com/photo.jpg", external_id="zara-123")
            assert path is not None
            assert path.is_file()
            assert path.read_bytes() == fake_bytes
            assert "zara-123" in path.name


def test_telegram_publisher_uses_multipart_for_local_image() -> None:
    """Verify TelegramPublisher sends multipart file when local image exists."""
    with tempfile.NamedTemporaryFile("wb", suffix=".jpg", delete=False) as f:
        f.write(b"valid-image-bytes-content" * 15)
        img_path = Path(f.name)

    try:
        post = ComposedPost(
            photo_url=str(img_path),
            text="Test post description",
            price=Decimal("99.99"),
            currency="USD",
            product_url="https://example.com/item",
            title="Linen Dress",
            source="zara",
        )

        publisher = TelegramPublisher(bot_token="fake-token", channel_id="@test_channel")

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.is_success = True
            mock_resp.json.return_value = {"ok": True, "result": {"message_id": 999}}
            mock_client.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            res = publisher.publish(post)
            assert res.success is True
            assert res.platform_post_id == "999"

            # Verify client.post was called with files argument (multipart)
            calls = mock_client.post.call_args_list
            assert len(calls) == 1
            call_kwargs = calls[0].kwargs
            assert "files" in call_kwargs
            assert "photo" in call_kwargs["files"]
    finally:
        if img_path.exists():
            img_path.unlink()

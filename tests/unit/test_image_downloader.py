"""Unit tests for ImageDownloader and local image publishing.

Per SRS FR-5 and SDD §3.6.
"""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch
import httpx
import pytest

from core.composer import ComposedPost
from core.image_downloader import ImageDownloader, high_resolution_image_url
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


def test_high_resolution_image_url_keeps_unknown_hosts() -> None:
    original = "https://example.com/photo.jpg?w=400"
    assert high_resolution_image_url(original) == original


def test_high_resolution_image_url_rewrites_store_thumbnails() -> None:
    zara = high_resolution_image_url(
        "https://static.zara.net/photos///2024/I/0/1/p/1/2/800/2/w/563/coat.jpg?ts=9"
    )
    assert "/w/2048/" in zara
    assert "ts=9" in zara

    already_large = high_resolution_image_url(
        "https://static.zara.net/photos///2024/I/0/1/p/1/2/800/2/w/2048/coat.jpg"
    )
    assert "/w/2048/" in already_large
    assert "/w/563/" not in already_large

    mango = high_resolution_image_url("https://media.mango.com/is/image/punto/37085988-99-001?imwidth=480")
    assert "imwidth=2048" in mango
    assert "imwidth=480" not in mango
    assert "qlt=90" in mango

    hm = high_resolution_image_url("https://image.hm.com/assets/hm/aa/aa/one.jpg")
    assert hm.endswith("?imwidth=2160")

    larger_hm = high_resolution_image_url("https://image.hm.com/assets/hm/aa/aa/one.jpg?imwidth=4000")
    assert "imwidth=4000" in larger_hm


def test_downloader_falls_back_when_high_resolution_url_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        downloader = ImageDownloader(dest_dir=tmp_dir)
        fake_bytes = b"fake-jpeg-image-bytes-1234567890" * 20
        source = "https://static.zara.net/photos/2/w/750/coat.jpg"

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            failed = MagicMock()
            failed.raise_for_status.side_effect = httpx.HTTPStatusError(
                "404",
                request=MagicMock(),
                response=MagicMock(),
            )
            ok = MagicMock()
            ok.content = fake_bytes
            ok.raise_for_status = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.get.side_effect = [failed, ok]
            mock_client_cls.return_value = mock_client

            path = downloader.download(source, external_id="coat")

        assert path is not None
        assert path.read_bytes() == fake_bytes
        requested = [call.args[0] for call in mock_client.get.call_args_list]
        assert "/w/2048/" in requested[0]
        assert requested[1] == source


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

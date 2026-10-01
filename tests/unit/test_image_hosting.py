"""Instagram must be able to download the photos from this project's own site."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from publishers.image_hosting import SelfHostedImageHost

JPEG = b"\xff\xd8\xff" + b"0" * 400


def _jpeg(tmp_path: Path, name: str = "mango-1_ig.jpg") -> Path:
    images = tmp_path / "images"
    images.mkdir(exist_ok=True)
    path = images / name
    path.write_bytes(JPEG)
    return path


def _crawler_sees(content: bytes, status: int = 200):
    def fake_client(*_args, **_kwargs):
        client = MagicMock()
        client.__enter__.return_value = client
        response = MagicMock()
        response.status_code = status
        response.content = content
        client.get.return_value = response
        return client

    return fake_client


def test_photo_already_served_by_the_site_needs_no_upload(tmp_path: Path) -> None:
    photo = _jpeg(tmp_path)
    fallback = MagicMock()
    host = SelfHostedImageHost(
        base_url="https://fashion-autopost.netlify.app/",
        images_dir=tmp_path / "images",
        fallback=fallback,
    )

    with patch("publishers.image_hosting.httpx.Client", side_effect=_crawler_sees(JPEG)):
        url = host.ensure_public_url(str(photo))

    assert url == "https://fashion-autopost.netlify.app/images/mango-1_ig.jpg"
    fallback.ensure_public_url.assert_not_called()


def test_upload_host_takes_over_when_the_site_does_not_serve_the_photo(tmp_path: Path) -> None:
    photo = _jpeg(tmp_path)
    fallback = MagicMock()
    fallback.ensure_public_url.return_value = "https://files.catbox.moe/abc.jpg"
    host = SelfHostedImageHost(
        base_url="https://fashion-autopost.netlify.app",
        images_dir=tmp_path / "images",
        fallback=fallback,
    )

    with patch("publishers.image_hosting.httpx.Client", side_effect=_crawler_sees(b"<html>404</html>", status=404)):
        url = host.ensure_public_url(str(photo))

    assert url == "https://files.catbox.moe/abc.jpg"
    fallback.ensure_public_url.assert_called_once_with(str(photo))


def test_a_file_outside_the_served_folder_goes_to_the_upload_host(tmp_path: Path) -> None:
    stray = tmp_path / "elsewhere.jpg"
    stray.write_bytes(JPEG)
    fallback = MagicMock()
    fallback.ensure_public_url.return_value = "https://files.catbox.moe/zzz.jpg"
    host = SelfHostedImageHost(
        base_url="https://fashion-autopost.netlify.app",
        images_dir=tmp_path / "images",
        fallback=fallback,
    )

    assert host.ensure_public_url(str(stray)) == "https://files.catbox.moe/zzz.jpg"


def test_a_photo_instagram_could_not_fetch_is_handed_to_the_upload_host(tmp_path: Path) -> None:
    photo = _jpeg(tmp_path)
    fallback = MagicMock()
    fallback.ensure_public_url.return_value = "https://files.catbox.moe/retry.jpg"
    host = SelfHostedImageHost(
        base_url="https://fashion-autopost.netlify.app",
        images_dir=tmp_path / "images",
        fallback=fallback,
    )

    retried = host.replace_unfetchable("https://fashion-autopost.netlify.app/images/mango-1_ig.jpg")

    assert retried == "https://files.catbox.moe/retry.jpg"
    fallback.ensure_public_url.assert_called_once_with(str(photo))

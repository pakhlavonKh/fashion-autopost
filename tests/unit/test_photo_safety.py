"""Empty pictures never reach the channel, and cut-out photos keep their product visible."""

from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from adapters.base import RawProduct
from config.app_config import AppConfig
from core.image_downloader import ImageDownloader, flatten_to_rgb, is_blank_image, prepare_original_jpeg
from core.pipeline import PipelineRunner
from core.pricing import FixedRateConverter
from tests.conftest import FakeLLMProvider, FakeProductRepository, FakePublisher


def _placeholder(path: Path) -> Path:
    Image.new("RGB", (2048, 3072), (245, 245, 247)).save(path, quality=90)
    return path


def _white_sneaker(path: Path) -> Path:
    image = Image.new("RGB", (2048, 3072), (244, 244, 244))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((600, 1500, 1500, 1900), 120, fill=(250, 250, 250))
    draw.rectangle((600, 1850, 1500, 1900), fill=(225, 222, 215))
    image.save(path, quality=90)
    return path


def _white_shirt(path: Path) -> Path:
    image = Image.new("RGB", (1500, 2000), (250, 250, 250))
    ImageDraw.Draw(image).polygon(
        [(400, 300), (1100, 300), (1250, 700), (1050, 750), (1050, 1800), (450, 1800), (450, 750), (250, 700)],
        fill=(246, 246, 246),
        outline=(215, 215, 215),
    )
    image.save(path, quality=90)
    return path


def _cutout(path: Path) -> Path:
    image = Image.new("RGBA", (1200, 1600), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((300, 800, 900, 1100), 80, fill=(252, 252, 252, 255))
    image.save(path)
    return path


def test_a_flat_placeholder_is_blank(tmp_path: Path) -> None:
    assert is_blank_image(_placeholder(tmp_path / "placeholder.jpg"))


def test_white_products_on_a_pale_backdrop_are_not_blank(tmp_path: Path) -> None:
    assert not is_blank_image(_white_sneaker(tmp_path / "sneaker.jpg"))
    assert not is_blank_image(_white_shirt(tmp_path / "shirt.jpg"))


def test_real_store_photos_are_never_blank() -> None:
    photos = sorted((Path(__file__).resolve().parents[2] / "data" / "diag_images").glob("*"))
    assert photos
    assert [photo.name for photo in photos if is_blank_image(photo)] == []


def test_a_cutout_lies_on_light_grey_and_keeps_its_white_product(tmp_path: Path) -> None:
    source = _cutout(tmp_path / "cutout.png")
    assert not is_blank_image(source)
    with Image.open(source) as image:
        flat = flatten_to_rgb(image)
    assert flat.getpixel((10, 10)) == (242, 242, 242)
    assert flat.getpixel((600, 900)) == (252, 252, 252)
    jpeg = prepare_original_jpeg(source, tmp_path / "cutout_tg.jpg")
    with Image.open(jpeg) as image:
        assert max(abs(a - b) for a, b in zip(image.getpixel((10, 10)), (242, 242, 242))) <= 2


def test_a_16_bit_photo_keeps_its_tones(tmp_path: Path) -> None:
    # Converted as is, Pillow clips every pixel of such a picture to white.
    source = tmp_path / "grey16.png"
    image = Image.new("I;16", (400, 600), 0xC2C2)
    image.paste(0x2323, (100, 200, 300, 400))
    image.save(source)
    assert not is_blank_image(source)
    with Image.open(source) as opened:
        flat = flatten_to_rgb(opened)
    assert flat.getpixel((10, 10)) == (194, 194, 194)
    assert flat.getpixel((200, 300)) == (35, 35, 35)


def test_download_all_drops_empty_pictures_and_says_so(tmp_path: Path) -> None:
    downloader = ImageDownloader(dest_dir=tmp_path)
    good = _white_sneaker(tmp_path / "good.jpg")
    empty = _placeholder(tmp_path / "empty.jpg")
    kept = downloader.download_all([str(good), str(empty)], external_id="x")
    assert kept == [good]
    assert downloader.last_blank == [str(empty)]


def test_manual_post_with_only_empty_pictures_is_not_published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = _placeholder(tmp_path / "empty.jpg")
    product = RawProduct(
        external_id="lefties-754376580-001",
        source="lefties",
        title="Retro sneakers",
        price=Decimal("29.99"),
        currency="EUR",
        photo_url=str(empty),
        product_url="https://www.lefties.com/es/en/retro-sneakers-c1030272270p754376580.html?colorId=001",
        in_stock=True,
        photo_urls=[str(empty)],
        color="White",
        sizes=("36", "37", "38"),
    )
    monkeypatch.setattr("core.pipeline.fetch_product_page", lambda url, headless=True: product)
    monkeypatch.setattr("core.pipeline.process_product_url_with_playwright", lambda url, headless=True: (url, ""))
    telegram = FakePublisher("telegram")

    class _Prompt:
        def load_prompt(self) -> str:
            return "write copy"

    runner = PipelineRunner(
        source=None,  # type: ignore[arg-type]
        repo=FakeProductRepository(),
        llm=FakeLLMProvider(select_count=1),
        fx=FixedRateConverter(fixed_rate=Decimal("1.08")),
        publishers=[telegram],
        config=AppConfig(),
        prompt_loader=_Prompt(),
    )
    runner.image_downloader = ImageDownloader(dest_dir=tmp_path / "images")
    ok, message = runner.publish_manual_url(product.product_url, custom_description="Кроссовки-60$")
    assert ok is False
    assert "пустые картинки" in message
    assert telegram.published_posts == []


def _jpeg(image: Image.Image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def _respond(*bodies: bytes):
    from unittest.mock import MagicMock

    responses = []
    for body in bodies:
        response = MagicMock()
        response.content = body
        response.raise_for_status = MagicMock()
        response.headers = {"content-type": "image/jpeg"}
        responses.append(response)
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.side_effect = responses
    return client


def test_an_empty_large_rendition_falls_back_to_the_page_link(tmp_path: Path) -> None:
    from unittest.mock import patch

    shoe = Image.new("RGB", (800, 1200), (244, 244, 244))
    ImageDraw.Draw(shoe).rounded_rectangle((150, 600, 650, 850), 60, fill=(200, 190, 170))
    empty = Image.new("RGB", (2048, 3072), (245, 245, 247))
    page_link = "https://static.lefties.com/assets/public/aa/bb/cc/dd/37254002001-a1o/37254002001-a1o.jpg?ts=17&w=850"
    client = _respond(_jpeg(empty), _jpeg(shoe))
    with patch("httpx.Client", return_value=client):
        path = ImageDownloader(dest_dir=tmp_path).download(page_link, external_id="shoe")
    requested = [call.args[0] for call in client.get.call_args_list]
    assert "w=2048" in requested[0]
    assert requested[1] == page_link
    assert path is not None and not is_blank_image(path)


def test_an_error_page_is_not_saved_as_a_photo(tmp_path: Path) -> None:
    from unittest.mock import patch

    shoe = Image.new("RGB", (800, 1200), (30, 30, 30))
    error_page = b"<html><body>Access denied</body></html>" * 20
    client = _respond(error_page, _jpeg(shoe))
    with patch("httpx.Client", return_value=client):
        path = ImageDownloader(dest_dir=tmp_path).download("https://cdn.example.com/p/shoe.jpg?w=400", external_id="shoe")
    assert path is not None
    with Image.open(path) as image:
        assert image.size == (800, 1200)
    assert [call.args[0] for call in client.get.call_args_list] == [
        "https://cdn.example.com/p/shoe.jpg?w=400",
        "https://cdn.example.com/p/shoe.jpg",
    ]


def test_every_form_empty_still_reports_the_empty_picture(tmp_path: Path) -> None:
    from unittest.mock import patch

    empty = _jpeg(Image.new("RGB", (2048, 3072), (245, 245, 247)))
    client = _respond(empty, empty)
    with patch("httpx.Client", return_value=client):
        downloader = ImageDownloader(dest_dir=tmp_path)
        kept = downloader.download_all(["https://cdn.example.com/p/shoe.jpg?w=400"], external_id="shoe")
    assert kept == []
    assert downloader.last_blank == ["https://cdn.example.com/p/shoe.jpg?w=400"]


def _telegram_post(photo_urls: list[str]):
    from core.composer import ComposedPost

    return ComposedPost(
        photo_url=photo_urls[0],
        text="Кроссовки-60$",
        price=Decimal("60"),
        currency="USD",
        product_url="https://www.lefties.com/es/en/retro-sneakers-c1030272270p754376580.html?colorId=001",
        title="Retro sneakers",
        source="lefties",
        photo_urls=photo_urls,
    )


def _telegram_client():
    from unittest.mock import MagicMock

    client = MagicMock()
    response = MagicMock()
    response.is_success = True
    response.json.return_value = {"ok": True, "result": {"message_id": 7}}
    client.__enter__.return_value = client
    client.post.return_value = response
    return client


def test_telegram_never_posts_an_empty_picture(tmp_path: Path) -> None:
    from unittest.mock import patch

    from publishers.telegram_publisher import TelegramPublisher

    empty = _placeholder(tmp_path / "empty.jpg")
    shoe = _white_sneaker(tmp_path / "shoe.jpg")
    publisher = TelegramPublisher(bot_token="token", channel_id="@channel")
    client = _telegram_client()
    with patch("httpx.Client", return_value=client):
        result = publisher.publish(_telegram_post([str(empty), str(shoe)]))
    assert result.success is True
    calls = client.post.call_args_list
    assert len(calls) == 1
    assert calls[0].args[0].endswith("/sendPhoto")


def test_telegram_does_not_hand_over_a_link_that_came_back_empty(tmp_path: Path) -> None:
    from unittest.mock import patch

    from publishers.telegram_publisher import TelegramPublisher

    empty = _jpeg(Image.new("RGB", (2048, 3072), (245, 245, 247)))
    link = "https://static.lefties.com/assets/public/aa/37254002001-a1o/37254002001-a1o.jpg?ts=17&w=850"
    publisher = TelegramPublisher(bot_token="token", channel_id="@channel")
    publisher.downloader = ImageDownloader(dest_dir=tmp_path)
    store = _respond(empty, empty, empty)
    sent = _telegram_client()
    # One httpx module serves both: the downloader follows redirects, the bot API client does not.
    with patch("httpx.Client", side_effect=lambda **kw: store if kw.get("follow_redirects") else sent):
        result = publisher.publish(_telegram_post([link]))
    assert len(store.get.call_args_list) == 3
    assert result.success is False
    assert sent.post.call_args_list == []


def test_download_all_says_what_happened_to_each_photo(tmp_path: Path) -> None:
    import shutil

    shoe = _white_sneaker(tmp_path / "shoe.jpg")
    copy = tmp_path / "shoe-copy.jpg"
    shutil.copy(shoe, copy)
    empty = _placeholder(tmp_path / "empty.jpg")
    chip = tmp_path / "chip.jpg"
    fabric = Image.new("RGB", (200, 200), (180, 40, 40))
    for x in range(0, 200, 8):
        ImageDraw.Draw(fabric).line((x, 0, x, 200), fill=(150, 30, 30), width=3)
    fabric.save(chip)
    missing = tmp_path / "missing.jpg"
    downloader = ImageDownloader(dest_dir=tmp_path / "out")
    kept = downloader.download_all([str(shoe), str(missing), str(empty), str(chip), str(copy)], external_id="x")
    assert kept == [shoe]
    assert downloader.last_report == [
        (str(shoe), "kept"),
        (str(missing), "failed"),
        (str(empty), "blank"),
        (str(chip), "swatch"),
        (str(copy), "duplicate"),
    ]


def _light_product(size: tuple[int, int], view: str) -> Image.Image:
    """A white sneaker or a pale shirt on a light backdrop, as stores photograph them."""
    width, height = size
    image = Image.new("RGB", size, (243, 243, 243))
    draw = ImageDraw.Draw(image)
    if view == "sneaker side":
        draw.rounded_rectangle((width * .15, height * .55, width * .85, height * .68), int(width * .05), fill=(250, 248, 244), outline=(225, 222, 215))
        draw.rectangle((width * .15, height * .66, width * .85, height * .70), fill=(200, 170, 120))
    elif view == "sneaker top":
        draw.ellipse((width * .30, height * .25, width * .48, height * .75), fill=(250, 248, 244), outline=(225, 222, 215))
        draw.ellipse((width * .52, height * .25, width * .70, height * .75), fill=(250, 248, 244), outline=(225, 222, 215))
    else:
        draw.polygon([(width * .27, height * .15), (width * .73, height * .15), (width * .83, height * .35), (width * .70, height * .38),
                      (width * .70, height * .9), (width * .30, height * .9), (width * .30, height * .38), (width * .17, height * .35)],
                     fill=(252, 252, 252), outline=(222, 222, 222))
    return image


@pytest.mark.parametrize("size", [(1000, 1000), (800, 800), (750, 1000), (600, 900)])
@pytest.mark.parametrize("view", ["sneaker side", "sneaker top", "shirt"])
def test_a_light_product_photo_of_medium_size_is_not_a_swatch(tmp_path: Path, size: tuple[int, int], view: str) -> None:
    # Before: dropped as a fabric swatch, so posts kept 2-4 of the store's photos.
    from core.image_downloader import is_material_or_color_swatch

    path = tmp_path / "photo.jpg"
    _light_product(size, view).save(path, quality=90)
    assert not is_material_or_color_swatch(path)


def test_real_swatches_are_still_dropped(tmp_path: Path) -> None:
    import random

    from core.image_downloader import is_material_or_color_swatch

    random.seed(1)
    stripes = Image.new("RGB", (600, 600), (235, 230, 220))
    for x in range(0, 600, 6):
        ImageDraw.Draw(stripes).line((x, 0, x, 600), fill=(215, 210, 200), width=2)
    knit = Image.new("RGB", (450, 450), (60, 70, 90))
    pixels = knit.load()
    for x in range(450):
        for y in range(450):
            shade = random.randint(-12, 12)
            pixels[x, y] = (60 + shade, 70 + shade, 90 + shade)
    for name, swatch in (("stripes", stripes), ("knit", knit), ("flat", Image.new("RGB", (500, 500), (180, 40, 40))), ("chip", Image.new("RGB", (200, 200), (20, 20, 20)))):
        path = tmp_path / f"{name}.jpg"
        swatch.save(path, quality=90)
        assert is_material_or_color_swatch(path), name

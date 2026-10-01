"""Carousel order: original looks first, product angles last, no duplicate photos."""

from pathlib import Path
import tempfile
from unittest.mock import patch

from PIL import Image, ImageDraw

from core.gallery import arrange_carousel, is_product_angle, ordered_photos
from core.image_downloader import ImageDownloader, unique_images


def test_product_angles_close_the_carousel_and_duplicates_collapse() -> None:
    looks = [f"https://media.mango.com/is/image/punto/10-99-{index:03d}" for index in range(1, 9)]
    urls = [
        *looks,
        "https://media.mango.com/is/image/punto/10-99-001?imwidth=400",
        "https://media.mango.com/is/image/punto/10-99-90",
        "https://media.mango.com/is/image/punto/10-99-91",
        "https://media.mango.com/is/image/punto/10-99-92",
        "https://media.mango.com/is/image/punto/10-99-020",
        "https://static.zara.net/photos/a/w/563/coat_1_1_1.jpg",
        "https://static.zara.net/photos/a/w/2048/coat_1_1_1.jpg",
    ]
    ordered = arrange_carousel(urls, max_photos=10)

    assert ordered[0] == "https://media.mango.com/is/image/punto/10-99-001?imwidth=400"
    assert ordered[1:7] == looks[1:7]
    assert ordered[-3:] == [
        "https://media.mango.com/is/image/punto/10-99-90",
        "https://media.mango.com/is/image/punto/10-99-91",
        "https://media.mango.com/is/image/punto/10-99-92",
    ]
    assert all("020" not in url for url in ordered)
    assert sum("10-99-001" in url for url in ordered) == 1

    zara = arrange_carousel([
        "https://static.zara.net/photos/a/w/563/coat_1_1_1.jpg",
        "https://static.zara.net/photos/a/w/2048/coat_1_1_1.jpg",
        "https://static.zara.net/photos/a/w/750/coat_2_1_1.jpg",
        "https://static.zara.net/photos/a/w/750/coat_3_1_1.jpg",
        "https://static.zara.net/photos/a/w/750/coat_3_2_1.jpg",
        "https://static.zara.net/photos/a/w/750/coat_3_3_1.jpg",
        "https://static.zara.net/photos/a/w/750/coat_6_1_1.jpg",
    ])
    assert [url.split("/")[-1] for url in zara] == [
        "coat_1_1_1.jpg",
        "coat_2_1_1.jpg",
        "coat_3_1_1.jpg",
        "coat_3_2_1.jpg",
        "coat_3_3_1.jpg",
    ]
    assert "/w/2048/" in zara[0]


def test_mango_front_then_detail_close_ups_close_the_carousel() -> None:
    base = "https://media.mango.com/is/image/punto/37007813-05-"
    page_order = ["002", "001", "003", "081", "084", "082", "023", "030", "900"]
    ordered = arrange_carousel([base + code for code in page_order], max_photos=10)
    assert [url.rsplit("-", 1)[-1] for url in ordered] == [
        "002", "001", "003", "081", "084", "082", "900", "023", "030",
    ]
    full = arrange_carousel(
        [base + f"{index:03d}" for index in range(1, 10)] + [base + "023", base + "030", base + "900"],
        max_photos=10,
    )
    assert len(full) == 10
    assert [url.rsplit("-", 1)[-1] for url in full[-3:]] == ["900", "023", "030"]


def test_pale_product_still_life_is_not_dropped_as_a_swatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        still = folder / "still.jpg"
        flat = folder / "flat.jpg"
        Image.new("RGB", (733, 1024), (236, 234, 230)).save(still)
        Image.new("RGB", (733, 1024), (150, 140, 130)).save(flat)
        urls = {
            "https://media.mango.com/is/image/punto/37007813-05-900": still,
            "https://media.mango.com/is/image/punto/37007813-05-004": flat,
        }
        downloader = ImageDownloader(dest_dir=folder)
        with patch.object(downloader, "download", side_effect=lambda url, external_id="": urls[url]):
            kept = downloader.download_all(list(urls), external_id="p")
    assert kept == [still]
    assert is_product_angle("https://media.mango.com/is/image/punto/37007813-05-900")
    assert is_product_angle("https://static.zara.net/photos/a/w/750/coat_3_2_1.jpg")
    assert not is_product_angle("https://media.mango.com/is/image/punto/37007813-05-004")


def test_zara_plain_shots_are_front_back_and_close() -> None:
    html = """
    {"kind":"full","path":"/assets/public/abc/item-p","name":"item-p"}
    {"kind":"other","path":"/assets/public/abc/item-a1","name":"item-a1"}
    {"kind":"plain","path":"/assets/public/abc/item-e0","name":"item-e0"}
    {"kind":"plain","path":"/assets/public/abc/item-e1","name":"item-e1"}
    {"kind":"plain","path":"/assets/public/abc/item-e2","name":"item-e2"}
    {"kind":"colorcut","path":"/assets/public/abc/item-c","name":"item-c"}
    """
    ordered = ordered_photos(html, "zara", "https://www.zara.com/es/item.html")
    assert [url.rsplit("/", 1)[-1] for url in ordered] == [
        "item-p.jpg",
        "item-a1.jpg",
        "item-e0.jpg",
        "item-e1.jpg",
        "item-e2.jpg",
    ]


def test_one_post_does_not_mix_other_mango_products() -> None:
    page = "https://shop.mango.com/it/it/p/donna/scarpe/stivaletti/botin-tacco/27011111/70/01"
    own = [
        "https://media.mango.com/is/image/punto/27011111-70-001",
        "https://media.mango.com/is/image/punto/27011111-70-002",
        "https://media.mango.com/is/image/punto/27011111-70-900",
    ]
    others = [
        "https://media.mango.com/is/image/punto/27022222-30-001",
        "https://media.mango.com/is/image/punto/27033333-99-001",
        "https://media.mango.com/is/image/punto/27011111-05-001",
    ]
    html = " ".join(own + others)
    ordered = ordered_photos(html, "mango", page, own[:1] + others)
    assert ordered
    assert all("27011111-70-" in url for url in ordered)
    assert not any(token in " ".join(ordered) for token in ("27022222", "27033333", "27011111-05"))


def test_identical_files_are_not_posted_twice() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        first = folder / "front.jpg"
        second = folder / "front-again.jpg"
        other = folder / "back.jpg"
        Image.new("RGB", (40, 60), (10, 20, 30)).save(first)
        Image.new("RGB", (80, 120), (10, 20, 30)).save(second)
        Image.new("RGB", (40, 60), (200, 10, 10)).save(other)
        kept = unique_images([first, second, other])
        assert kept == [first, other]


def test_the_same_shot_at_two_exposures_is_posted_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        shot = Image.new("RGB", (200, 300), (60, 60, 60))
        ImageDraw.Draw(shot).rectangle((40, 60, 160, 240), fill=(190, 185, 180))
        front = folder / "front.jpg"
        relit = folder / "front-relit.jpg"
        shot.save(front, quality=92)
        shot.point(lambda level: min(255, level + 28)).save(relit, quality=80)

        # Same two tones, different composition: a real second angle, not a copy.
        back_shot = Image.new("RGB", (200, 300), (190, 185, 180))
        ImageDraw.Draw(back_shot).rectangle((0, 120, 200, 300), fill=(60, 60, 60))
        back = folder / "back.jpg"
        back_shot.save(back, quality=92)

        assert unique_images([front, relit, back]) == [front, back]

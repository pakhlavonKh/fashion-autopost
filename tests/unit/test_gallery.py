"""Carousel order: original looks first, product angles last, no duplicate photos."""

from pathlib import Path
import tempfile

from PIL import Image

from core.gallery import arrange_carousel, ordered_photos
from core.image_downloader import unique_images


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

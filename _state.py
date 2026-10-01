import os
import sys

import httpx
from dotenv import load_dotenv

sys.stdout = open("_state.txt", "w", encoding="utf-8")
load_dotenv(".env")
key = os.getenv("DASHBOARD_ADMIN_PASSWORD") or "fashion-admin-2026"
headers = {"X-Admin-Key": key, "Authorization": f"Bearer {key}"}

with httpx.Client(base_url="http://194.62.52.152", headers=headers, timeout=60.0) as c:
    stats = c.get("/api/stats").json()
    print("products:", stats.get("total_products"), "| published:", stats.get("published_total"), "| today:", stats.get("published_today"))

    products = c.get("/api/products?limit=300").json().get("products", [])
    queue = [p for p in products if p.get("status") == "new" and not p.get("telegram_post_id")]
    print("rows:", len(products), "| queue ready to publish:", len(queue))
    by_brand: dict[str, int] = {}
    for p in products:
        by_brand[str(p.get("source"))] = by_brand.get(str(p.get("source")), 0) + 1
    print("by brand:", by_brand)

    # Does the server already run the newest code?
    print("collect endpoint deployed:", c.get("/api/collect-products").status_code)
    print("publish endpoint deployed:", c.get("/api/publish-now").status_code, "(405 = exists)")

"""
Stock & price checker for amazon.in, flipkart.com, blinkit.com.
Sends a Telegram message (and optionally email) when an item comes
back in stock or drops below your target price.

Products are configured in products.json. Last-known state is kept in
state.json so you are only notified on a change, not every run.
"""

import json
import os
import re
import smtplib
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
PRODUCTS_FILE = BASE / "products.json"
STATE_FILE = BASE / "state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

IN_STOCK = "IN_STOCK"
OUT_OF_STOCK = "OUT_OF_STOCK"
BLOCKED = "BLOCKED"
ERROR = "ERROR"


# ---------------------------------------------------------------- fetching

def fetch(url, headers=None):
    """Return page HTML, or None if blocked/unreachable."""
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=headers or HEADERS, timeout=30)
            if resp.status_code in (403, 429, 503):
                if attempt == 0:
                    time.sleep(5)
                    continue
                return None
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            print(f"  fetch error: {exc}")
            if attempt == 0:
                time.sleep(5)
    return None


# ---------------------------------------------------------------- parsing

def parse_price(text):
    """'₹1,23,456.00' -> 123456.0"""
    if not text:
        return None
    m = re.search(r"([\d,]+(?:\.\d+)?)", text.replace(",", ""))
    return float(m.group(1)) if m else None


def price_from_ldjson(soup):
    """Most e-commerce pages embed schema.org JSON-LD with an offers.price."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            offers = item.get("offers") if isinstance(item, dict) else None
            if isinstance(offers, list):
                offers = offers[0] if offers else None
            if isinstance(offers, dict) and offers.get("price"):
                return parse_price(str(offers["price"]))
    return None


def check_amazon(html):
    low = html.lower()
    if "api-services-support@amazon.com" in low or "captcha" in low:
        return BLOCKED, None
    soup = BeautifulSoup(html, "html.parser")

    price = None
    for selector in (
        "#corePriceDisplay_desktop_feature_div span.a-price-whole",
        "#corePrice_feature_div span.a-offscreen",
        "span.a-price span.a-offscreen",
        "span.a-price-whole",
    ):
        for el in soup.select(selector):
            price = parse_price(el.get_text())
            if price:
                break
        if price:
            break
    if price is None:
        price = price_from_ldjson(soup)

    availability = soup.select_one("#availability")
    avail_text = availability.get_text(" ", strip=True).lower() if availability else ""
    if "unavailable" in avail_text or "out of stock" in avail_text:
        return OUT_OF_STOCK, price
    if soup.select_one("#add-to-cart-button") or "in stock" in avail_text:
        return IN_STOCK, price
    return OUT_OF_STOCK, price


def check_flipkart(html):
    """Flipkart (fetched with a mobile UA) embeds its state as JSON in the
    page: "availabilityStatus":"IN_STOCK" and "finalPrice":59900."""
    price = None
    m = re.search(r'"finalPrice"\s*:\s*(\d+)', html)
    if not m:
        m = re.search(r'"price"\s*:\s*(\d+)', html)
    if m:
        price = float(m.group(1))

    m = re.search(r'"availabilityStatus"\s*:\s*"(\w+)"', html)
    if m:
        return (IN_STOCK if m.group(1) == "IN_STOCK" else OUT_OF_STOCK), price

    # No product JSON found — either a bot wall or the page shape changed.
    # ("access denied" can appear inside Flipkart's JS bundles, so only
    # treat it as a block when the product data is missing too.)
    low = html.lower()
    if "access denied" in low or "unusual traffic" in low or len(html) < 50000:
        return BLOCKED, None
    if "sold out" in low or "notify me" in low or "coming soon" in low:
        return OUT_OF_STOCK, price
    if "add to cart" in low or "buy now" in low:
        return IN_STOCK, price
    return ERROR, price


def check_blinkit(url, lat, lon):
    """Blinkit stock is per dark store, so we call the same JSON API the
    website uses, passing your location as lat/lon headers. The response
    also contains recommended products, so match on the product id."""
    m = re.search(r"/prid/(\d+)", url)
    if not m:
        print("  blinkit url must contain /prid/<id>")
        return ERROR, None
    prid = m.group(1)

    headers = dict(HEADERS, Accept="application/json", lat=str(lat), lon=str(lon))
    try:
        resp = requests.post(
            f"https://blinkit.com/v1/layout/product/{prid}",
            headers=headers, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  blinkit api error: {exc}")
        return BLOCKED, None

    matches = []

    def walk(node):
        if isinstance(node, dict):
            if str(node.get("product_id")) == prid and "inventory" in node:
                matches.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    if not matches:
        return ERROR, None

    # Prefer an entry with a numeric price; inventory > 0 means in stock.
    best = max(matches, key=lambda n: isinstance(n.get("price"), (int, float)))
    price = best.get("price") or None
    inventory = best.get("inventory") or 0
    return (IN_STOCK if inventory > 0 else OUT_OF_STOCK), (
        float(price) if price else None
    )


MOBILE_HEADERS = dict(
    HEADERS,
    **{
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
        )
    },
)


def check_product(product, location):
    url = product["url"]
    if "amazon." in url:
        html = fetch(url)
        return check_amazon(html) if html else (BLOCKED, None)
    if "flipkart.com" in url:
        # Flipkart only server-renders product data for mobile browsers
        html = fetch(url, headers=MOBILE_HEADERS)
        return check_flipkart(html) if html else (BLOCKED, None)
    if "blinkit.com" in url:
        lat = location.get("lat") or "28.6139"
        lon = location.get("lon") or "77.2090"
        return check_blinkit(url, lat, lon)
    print(f"  unsupported site: {url}")
    return ERROR, None


# ---------------------------------------------------------------- notify

def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("  (telegram not configured, skipping)")
        return False
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=30,
    )
    ok = resp.ok and resp.json().get("ok")
    print(f"  telegram: {'sent' if ok else 'FAILED ' + resp.text[:200]}")
    return ok


def send_email(subject, body):
    address = os.environ.get("EMAIL_ADDRESS")
    password = os.environ.get("EMAIL_PASSWORD")
    to = os.environ.get("EMAIL_TO", address)
    if not address or not password:
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = to
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(address, password)
            server.sendmail(address, [to], msg.as_string())
        print("  email: sent")
        return True
    except Exception as exc:
        print(f"  email: FAILED {exc}")
        return False


def notify(message):
    sent = send_telegram(message)
    sent = send_email("Stock Alert", message) or sent
    return sent


# ---------------------------------------------------------------- main

def main():
    config = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
    products = config["products"]

    location = config.get("blinkit_location", {})

    state = {}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))

    for product in products:
        name, url = product["name"], product["url"]
        target = product.get("target_price")
        print(f"Checking: {name}")

        status, price = check_product(product, location)
        price_str = f"₹{price:,.0f}" if price else "price unknown"
        print(f"  -> {status}, {price_str}")

        prev = state.get(url, {})
        prev_status = prev.get("status")
        prev_price_alerted = prev.get("price_alerted", False)

        if status in (BLOCKED, ERROR):
            # Don't overwrite known state on a failed run; just skip.
            time.sleep(3)
            continue

        # Back in stock (was out / unknown before)
        if status == IN_STOCK and prev_status != IN_STOCK:
            notify(f"🟢 BACK IN STOCK\n{name}\n{price_str}\n{url}")

        # Price target hit (only alert once until it goes back above target)
        price_alerted = prev_price_alerted
        if target and price is not None:
            if price <= target and not prev_price_alerted:
                notify(
                    f"🔔 PRICE ALERT\n{name}\nNow {price_str} "
                    f"(target ₹{target:,.0f})\n{url}"
                )
                price_alerted = True
            elif price > target:
                price_alerted = False

        state[url] = {"status": status, "price": price, "price_alerted": price_alerted}
        time.sleep(3)  # be polite between requests

    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print("Done.")


if __name__ == "__main__":
    sys.exit(main())

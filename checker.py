"""
Stock & price checker for amazon.in and flipkart.com.
Sends a Telegram message (and optionally email) when an item comes
back in stock, its price changes, or it drops below a target price.

Products are configured in products.json. Last-known state is kept in
state.json so you are only notified on a change, not every run.
"""

import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime
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
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Cache-Control": "max-age=0",
}

# --stock-only on the command line: alert only on stock changes,
# ignore price movements entirely
STOCK_ONLY = "--stock-only" in sys.argv

IN_STOCK = "IN_STOCK"
OUT_OF_STOCK = "OUT_OF_STOCK"
BLOCKED = "BLOCKED"
ERROR = "ERROR"


# ---------------------------------------------------------------- fetching

def fetch(url, headers=None, attempts=3):
    """Return page HTML, or None if blocked/unreachable."""
    for attempt in range(attempts):
        try:
            resp = requests.get(url, headers=headers or HEADERS, timeout=30)
            if resp.status_code in (403, 429, 503):
                print(f"  got HTTP {resp.status_code} (attempt {attempt + 1}/{attempts})")
                if attempt < attempts - 1:
                    time.sleep(5 + attempt * 5)
                    continue
                return None
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            print(f"  fetch error: {exc}")
            if attempt < attempts - 1:
                time.sleep(5)
    return None


def check_amazon_url(url):
    """Amazon is picky about datacenter and mobile-carrier IPs. Resolve
    short links to the real /dp/ URL, try with desktop headers, then fall
    back to a mobile browser identity if blocked."""
    # amzn.in short links redirect to the full product page; resolving
    # them first avoids an extra hop on every retry
    if "amzn.in" in url or "amzn.to" in url:
        try:
            resp = requests.head(url, headers=HEADERS, timeout=30, allow_redirects=True)
            if "/dp/" in resp.url:
                url = resp.url.split("?")[0]
                print(f"  resolved to {url}")
        except requests.RequestException:
            pass

    html = fetch(url)
    if html:
        status, price = check_amazon(html)
        if status != BLOCKED:
            return status, price
        print("  captcha with desktop headers, retrying as mobile browser...")
    html = fetch(url, headers=MOBILE_HEADERS, attempts=2)
    return check_amazon(html) if html else (BLOCKED, None)


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

    # Only read the price from the main buy-box areas — generic price spans
    # can belong to unrelated widgets (accessories, bundles) and would
    # report a wrong price when the main listing has none.
    price = None
    for selector in (
        "#corePriceDisplay_desktop_feature_div span.a-price-whole",
        "#corePrice_feature_div span.a-offscreen",
        "#corePriceDisplay_mobile_feature_div span.a-price-whole",
        "#apex_desktop span.a-price span.a-offscreen",
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


MOBILE_HEADERS = dict(
    HEADERS,
    **{
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
        )
    },
)


# ------------------------------------------------------- search monitoring

def amazon_search_results(url):
    """Yield (item_id, title, price, link) for every result on an Amazon
    search page."""
    # Amazon sometimes serves a JS-only page variant with no results in
    # the HTML, and it can stick briefly for one browser fingerprint —
    # alternate desktop/mobile identities until we get the rendered one
    for attempt, hdrs in enumerate((HEADERS, MOBILE_HEADERS, HEADERS)):
        html = fetch(url, headers=hdrs)
        if html is None:
            return None
        soup = BeautifulSoup(html, "html.parser")
        results = []
        for div in soup.select('div[data-asin][data-component-type="s-search-result"]'):
            asin = div.get("data-asin", "").strip()
            if not asin:
                continue
            title_el = div.select_one("h2")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            price_el = div.select_one("span.a-price span.a-offscreen")
            price = parse_price(price_el.get_text()) if price_el else None
            results.append((asin, title, price, f"https://www.amazon.in/dp/{asin}"))
        if results:
            return results
        time.sleep(3)
    return results


def flipkart_search_results(url):
    """Yield (item_id, title, price, link) from a Flipkart search page.
    Titles come from the URL slugs; prices are matched from nearby text."""
    html = fetch(url, headers=MOBILE_HEADERS)
    if html is None:
        return None
    results = []
    seen = set()
    for m in re.finditer(r'href="(/([^"?]*?)/p/(itm\w+))[^"]*"', html):
        path, slug, pid = m.group(1), m.group(2), m.group(3)
        if pid in seen:
            continue
        seen.add(pid)
        title = slug.replace("-", " ")
        # look for a price shortly after the link in the HTML
        window = html[m.end() : m.end() + 3000]
        pm = re.search(r"₹\s*([\d,]+)", window)
        price = parse_price(pm.group(1)) if pm else None
        results.append((pid, title, price, f"https://www.flipkart.com{path}"))
    return results


def run_searches(searches, state):
    """Alert when a NEW listing matching the keywords appears in search
    results — catches resellers posting the product under a fresh listing
    that our watched URLs would miss."""
    for search in searches:
        name = search["name"]
        url = search["url"]
        must = [w.lower() for w in search.get("must_include", [])]
        any_of = [w.lower() for w in search.get("any_include", [])]
        exclude = [w.lower() for w in search.get("exclude", [])]
        min_price = search.get("min_price")
        max_price = search.get("max_price")
        print(f"Searching: {name}")

        if "amazon." in url:
            results = amazon_search_results(url)
        elif "flipkart.com" in url:
            results = flipkart_search_results(url)
        else:
            print("  unsupported search site")
            continue
        if results is None:
            print("  search page blocked, skipping")
            continue

        matching = {}
        for item_id, title, price, link in results:
            low = title.lower()
            if not all(w in low for w in must):
                continue
            if any_of and not any(w in low for w in any_of):
                continue
            if any(w in low for w in exclude):
                continue
            if price is not None:
                if min_price and price < min_price:
                    continue
                if max_price and price > max_price:
                    continue
            matching[item_id] = (title, price, link)

        key = f"search::{name}"
        prev_seen = state.get(key, {}).get("seen")
        print(f"  {len(results)} results, {len(matching)} match")

        if prev_seen is None:
            # First run: record what exists today without alerting
            state[key] = {"seen": sorted(matching)}
            time.sleep(3)
            continue

        new_ids = [i for i in matching if i not in prev_seen]
        for item_id in new_ids[:5]:
            title, price, link = matching[item_id]
            price_str = f"₹{price:,.0f}" if price else "price unknown"
            notify(
                f"🆕 NEW LISTING FOUND\n{title[:120]}\n{price_str}\n"
                f"Found via search: {name}",
                order_url=link,
            )
        state[key] = {"seen": sorted(set(prev_seen) | set(matching))}
        time.sleep(3)


def check_product(product):
    url = product["url"]
    if "amazon." in url or "amzn.in" in url or "amzn.to" in url:
        return check_amazon_url(url)
    if "flipkart.com" in url:
        # Flipkart only server-renders product data for mobile browsers
        html = fetch(url, headers=MOBILE_HEADERS)
        return check_flipkart(html) if html else (BLOCKED, None)
    print(f"  unsupported site: {url}")
    return ERROR, None


# ---------------------------------------------------------------- notify

def send_telegram(message, order_url=None):
    """TELEGRAM_CHAT_ID may hold several ids separated by commas,
    e.g. "7537197073,1234567890" — everyone gets the alert.
    If order_url is given, the message carries a "Place Order Now" button
    (product links open directly in the Amazon/Flipkart app on phones)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_ids:
        print("  (telegram not configured, skipping)")
        return False
    payload = {"text": message, "disable_web_page_preview": True}
    if order_url:
        payload["reply_markup"] = json.dumps(
            {"inline_keyboard": [[{"text": "🛒 Place Order Now", "url": order_url}]]}
        )
    any_sent = False
    for chat_id in [c.strip() for c in chat_ids.split(",") if c.strip()]:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=dict(payload, chat_id=chat_id),
            timeout=30,
        )
        ok = resp.ok and resp.json().get("ok")
        print(f"  telegram -> {chat_id}: {'sent' if ok else 'FAILED ' + resp.text[:200]}")
        any_sent = any_sent or ok
    return any_sent


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


def notify(message, order_url=None):
    sent = send_telegram(message, order_url=order_url)
    sent = send_email("Stock Alert", message) or sent
    return sent


# ---------------------------------------------------------------- main

def main():
    config = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8-sig"))
    products = config["products"]

    state = {}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))

    for product in products:
        name, url = product["name"], product["url"]
        target = product.get("target_price")
        print(f"Checking: {name}")

        status, price = check_product(product)
        price_str = f"₹{price:,.0f}" if price else "price unknown"
        print(f"  -> {status}, {price_str}")

        prev = state.get(url, {})
        prev_status = prev.get("status")
        prev_price = prev.get("price")
        prev_pending = prev.get("pending_price")
        prev_price_alerted = prev.get("price_alerted", False)

        if status in (BLOCKED, ERROR):
            # Don't overwrite known state on a failed run; just skip.
            time.sleep(3)
            continue

        # Back in stock (was out / unknown before) — the big one
        if status == IN_STOCK and prev_status != IN_STOCK:
            notify(
                f"🟢🟢 BACK IN STOCK 🟢🟢\n\n{name}\n{price_str}\n\n"
                f"GO GO GO — tap the button below!",
                order_url=url,
            )

        # Listing changed: price moved up or down (reviews/ratings are
        # never tracked, so they can't trigger anything). A new price must
        # be seen on two consecutive cycles before alerting, so a one-off
        # misread page never sends a false alert.
        pending_price = None
        stored_price = price
        if (
            not STOCK_ONLY
            and prev_status is not None
            and price and prev_price and price != prev_price
        ):
            if prev_pending == price:
                direction = "📉 dropped" if price < prev_price else "📈 increased"
                notify(
                    f"✏️ LISTING CHANGED\n{name}\n"
                    f"Price {direction}: ₹{prev_price:,.0f} → {price_str}",
                    order_url=url,
                )
            else:
                print(f"  price changed (₹{prev_price:,.0f} → {price_str}), waiting for confirmation next cycle")
                pending_price = price
                stored_price = prev_price  # keep old price until confirmed

        # Optional price target (only alert once until it rises back above)
        price_alerted = prev_price_alerted
        if not STOCK_ONLY and target and price is not None:
            if price <= target and not prev_price_alerted:
                notify(
                    f"🔔 PRICE TARGET HIT\n{name}\nNow {price_str} "
                    f"(target ₹{target:,.0f})",
                    order_url=url,
                )
                price_alerted = True
            elif price > target:
                price_alerted = False

        state[url] = {
            "status": status,
            "price": stored_price,
            "pending_price": pending_price,
            "price_alerted": price_alerted,
        }
        time.sleep(3)  # be polite between requests

    run_searches(config.get("searches", []), state)

    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Cycle done at {datetime.now():%Y-%m-%d %H:%M:%S}")


if __name__ == "__main__":
    if "--loop" in sys.argv:
        # Run forever, checking every 5 minutes (for phone/PC hosting).
        # Override with e.g. --loop 120 for every 2 minutes.
        args = [a for a in sys.argv[1:] if a not in ("--loop", "--stock-only")]
        interval = int(args[0]) if args and args[0].isdigit() else 300
        mode = "stock alerts only" if STOCK_ONLY else "stock + price alerts"
        print(f"Loop mode: checking every {interval}s ({mode}). Ctrl+C to stop.")
        cycle = 0
        while True:
            cycle += 1
            print(f"\n=== Cycle {cycle} started at {datetime.now():%Y-%m-%d %H:%M:%S} ===")
            try:
                main()
            except Exception as exc:
                print(f"run failed: {exc}")
            print(f"Next check at {datetime.fromtimestamp(time.time() + interval):%H:%M:%S}")
            time.sleep(interval)
    else:
        sys.exit(main())

# Stock Alert Bot

Checks products on **amazon.in**, **flipkart.com**, and **blinkit.com** every 5 minutes
and sends a Telegram message (and optionally an email) when an item comes back
**in stock** or drops below your **target price**.

Runs free, 24/7, on GitHub Actions — no server, no credit card.

---

## 1. Set up the Telegram bot (2 minutes)

1. In Telegram, message **@BotFather** → send `/newbot` → pick a name and username.
   BotFather replies with a **bot token** like `123456789:AAF-abc...`. Save it.
2. Open a chat with your new bot and send it any message (e.g. "hi").
   This is required — bots can't message you first.
3. Get your **chat id**: open this URL in a browser (with your token filled in):
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   Look for `"chat":{"id":123456789` — that number is your chat id.

## 2. Add your products

Edit `products.json`:

```json
{
  "blinkit_location": { "lat": "28.6139", "lon": "77.2090" },
  "products": [
    {
      "name": "PS5 Slim",
      "url": "https://www.amazon.in/dp/B0CQKJQVLK",
      "target_price": 40000
    }
  ]
}
```

- `url` — paste the full product page link from any of the 3 sites.
- `target_price` — optional. Set to a number to also get a "price dropped" alert,
  or `null` to only get in-stock alerts.
- `blinkit_location` — Blinkit stock depends on your area. Put your latitude/longitude
  (Google Maps → right-click your home → copy coordinates). Leave empty to skip.

## 3. Put it on GitHub

1. Create a **public** repo (public = unlimited free Actions minutes).
2. Push these files to it:
   ```
   git init
   git add .
   git commit -m "stock alert bot"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
3. In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
   Add:
   | Secret | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | token from BotFather |
   | `TELEGRAM_CHAT_ID` | your chat id |
   | `EMAIL_ADDRESS` | *(optional)* your Gmail address |
   | `EMAIL_PASSWORD` | *(optional)* a Gmail **App Password** (not your real password) |
   | `EMAIL_TO` | *(optional)* where to send alerts (defaults to EMAIL_ADDRESS) |

4. Go to the **Actions** tab → enable workflows → open **Stock Check** →
   **Run workflow** to test it once manually. Check the run logs.

That's it. It now runs every ~5 minutes automatically.

## Testing locally first (recommended)

```powershell
pip install -r requirements.txt
$env:TELEGRAM_BOT_TOKEN = "your-token"
$env:TELEGRAM_CHAT_ID  = "your-chat-id"
python checker.py
```

Tip: to force a test notification, run it once, then edit `state.json` and change a
product's `"status"` to `"OUT_OF_STOCK"` — the next run will alert if it's in stock.

## How it works

- Fetches each product page with browser-like headers.
- Site-specific checks: Amazon (`#availability` / add-to-cart button),
  Flipkart ("sold out" / "notify me" text), Blinkit ("out of stock" text + your location cookie).
- Price is read from the page (or embedded JSON-LD).
- `state.json` remembers the last status, so you're alerted **only when something
  changes** — not every 5 minutes. The workflow commits it back to the repo.

## Known limitations (honest notes)

- **GitHub cron isn't exact** — "every 5 min" usually means every 5–15 min under load.
- **Amazon sometimes CAPTCHAs datacenter IPs.** The script detects this and skips that
  run instead of reporting wrong info; it usually succeeds on later runs.
- **Blinkit stock is hyper-local** and their page layout changes often — the location
  cookie is best-effort. If it misreports, tell me and we can switch to their API.
- **Scheduled workflows pause after ~60 days of no repo activity.** The bot's own
  state commits count as activity, so this rarely triggers — but if alerts ever stop,
  check the Actions tab for a "re-enable workflow" banner.
- Site HTML changes over time. If a product is always reported one way, run the
  workflow manually and read the logs — the fix is usually a one-line selector update.

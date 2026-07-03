"""Real-world task: price-compare a product across 3 US retailers.

Burns ONE free-tier cloud-browser session. Visits Newegg, Walmart,
and Target to find the price of the Sony WH-1000XM5 wireless
noise-cancelling headphones. No login required.

Tactics for the free tier (heavy retailer anti-bot):
  * Skip the homepage — go directly to each store's search-results
    URL via the URL bar (no search-box interaction, no cookie
    banner, no homepage redirects).
  * Lower max_steps so we fail fast and report partial results.
  * flash_mode=False for deliberate navigation on JS-heavy pages.
  * Accept "blocked" / "captcha" as a legitimate outcome.

Run with the venv:
    .venv/bin/python scripts/smoke_price_compare.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Match api.py/CLI convention: cwd-relative dotenv load
load_dotenv(find_dotenv(usecwd=True))

# Make src/ importable when running from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

from core.browser_use_tool import browser_use_task  # noqa: E402

PRODUCT = "Sony WH-1000XM5 wireless noise-cancelling headphones"
# Direct search-result URLs — skip homepage, cookie banner, search-box interaction
SEARCH_URLS = {
    "newegg": "https://www.newegg.com/p/pl?d=sony+wh-1000xm5",
    "walmart": "https://www.walmart.com/search?q=sony+wh-1000xm5",
    "target": "https://www.target.com/s?searchTerm=sony+wh-1000xm5",
}


async def main() -> int:
    if not os.environ.get("BROWSER_USE_API_KEY"):
        print("BROWSER_USE_API_KEY not set; aborting", file=sys.stderr)
        return 2

    print("=" * 60)
    print(f"REAL TASK: price-compare {PRODUCT!r}")
    print(f"Stores:   {', '.join(SEARCH_URLS)}")
    print("Tactic:   direct search-URL navigation, no homepage/cookie banner")
    print("=" * 60)

    task = (
        f"Find the price of the {PRODUCT} at three US retailers. "
        f"For EACH of these three stores, do this:\n"
        f"  1. Navigate DIRECTLY to the search-results URL (the homepage "
        f"and cookie banners are skipped):\n"
        f"     - Newegg:   {SEARCH_URLS['newegg']}\n"
        f"     - Walmart:  {SEARCH_URLS['walmart']}\n"
        f"     - Target:   {SEARCH_URLS['target']}\n"
        f"  2. From the search results, click the first product listing "
        f"that is the Sony WH-1000XM5 (black, standard version, not a "
        f"different color or a bundle).\n"
        f"  3. On the product page, extract: the exact price (string like "
        f"'$348.00' or '$399.99'), the product title, and whether the "
        f"page shows it as in stock / available.\n"
        f"  4. If a captcha or 'verify you are human' challenge appears, "
        f"record status='blocked' for that store and STOP processing that "
        f"store. Do NOT try to solve captchas. Move on to the next store.\n"
        f"  5. If the page is still loading after 30 seconds or shows a "
        f"cloudflare/error page, record status='timeout' or "
        f"status='blocked' and move on.\n"
        f"  6. Be efficient: at most ~5 steps per store (navigate -> click "
        f"first result -> read price). Do not scroll aimlessly or read "
        f"reviews.\n"
        f"\n"
        f"When done, output a single JSON object with one entry per store "
        f"plus a 'cheapest' string ('newegg' | 'walmart' | 'target' | 'none') "
        f"and a 'summary' one-sentence string. If NO store returned a "
        f"price, set cheapest='none' and explain in summary."
    )

    result = await browser_use_task.ainvoke(
        {
            "task": task,
            "max_steps": 20,  # fail-fast budget
            "flash_mode": False,  # deliberate navigation on JS-heavy sites
            "output_schema": {
                "newegg": (
                    "object: status ('found'|'blocked'|'timeout'|'not_found'|'error'), "
                    "price (string like '$348.00' or empty), title (string or empty), "
                    "available (boolean or null), url (string, the product page URL or empty)"
                ),
                "walmart": "object: status, price, title, available, url",
                "target": "object: status, price, title, available, url",
                "cheapest": (
                    "string: 'newegg' | 'walmart' | 'target' | 'none' — "
                    "which store had the lowest price"
                ),
                "summary": (
                    "string: one-sentence summary of what was found and "
                    "where the best price is, or what blocked the search"
                ),
            },
        }
    )

    print()
    print("=" * 60)
    print("RESULT")
    print("=" * 60)
    print(f"success:   {result.success}")
    print(f"steps:     {result.steps_taken}")
    print(f"urls:      {result.urls_visited}")
    if result.error:
        print(f"error:     {result.error}")
    print()
    print("extracted:")
    print(json.dumps(result.extracted, indent=2, ensure_ascii=False))
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

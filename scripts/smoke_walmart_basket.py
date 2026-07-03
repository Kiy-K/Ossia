"""Real-world smoke test: Walmart basket task.

Burns ONE free-tier cloud-browser session. Searches Walmart for 5
items and tries to add each to the cart. Will likely hit a login
wall on the add-to-cart step (fresh cloud browser has no cookies);
the structured output surfaces exactly which items got found and
which step blocked.

Run with the venv:
    .venv/bin/python scripts/smoke_walmart_basket.py
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

ITEMS = ["milk", "tea", "cereal", "bread", "bananas"]


async def main() -> int:
    if not os.environ.get("BROWSER_USE_API_KEY"):
        print("BROWSER_USE_API_KEY not set; aborting", file=sys.stderr)
        return 2

    print("=" * 60)
    print("REAL TASK: Walmart basket (milk, tea, cereal, bread, bananas)")
    print("Note: cloud browser is unauthenticated — add-to-cart will")
    print("likely hit a login wall. The script reports what works.")
    print("=" * 60)

    task = (
        "Go to https://www.walmart.com. For EACH of these items in order — "
        "milk, tea, cereal, bread, bananas — do the following:\n"
        "  1. Search for the item using the search box.\n"
        "  2. Click the first product result that looks like the basic "
        "version of the item (e.g. for 'milk' pick a regular gallon, not "
        "chocolate milk; for 'cereal' pick a plain boxed cereal, not a "
        "variety pack).\n"
        "  3. On the product page, attempt to click the 'Add to cart' button.\n"
        "  4. If a sign-in / login / account-required modal appears, that is "
        "EXPECTED — note it in the per-item status and move on to the next "
        "item. Do NOT try to create an account or fill in credentials.\n"
        "  5. After all five items, report your final result as a JSON "
        "object with one entry per item.\n"
    )

    result = await browser_use_task.ainvoke(
        {
            "task": task,
            "max_steps": 30,
            "flash_mode": False,  # careful navigation for a complex multi-step task
            "output_schema": {
                "milk": "object: status and details for the milk item",
                "tea": "object: status and details for the tea item",
                "cereal": "object: status and details for the cereal item",
                "bread": "object: status and details for the bread item",
                "bananas": "object: status and details for the bananas item",
                "summary": "one-sentence summary of the overall run",
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
    print(f"final:     {result.final_result[:300]!r}")
    print(f"extracted: {json.dumps(result.extracted, indent=2, ensure_ascii=False)}")
    if result.error:
        print(f"error:     {result.error}")
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

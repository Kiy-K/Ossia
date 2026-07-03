"""Smoke test for the real browser-use cloud browser.

Burns ONE free-tier task. Targets https://example.com — the canonical
minimal page with a known title ("Example Domain"). Uses
output_schema so we verify the structured-extraction path on the
first try.

Run with the venv:
    .venv/bin/python scripts/smoke_browser_use.py
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


async def main() -> int:
    if not os.environ.get("BROWSER_USE_API_KEY"):
        print("BROWSER_USE_API_KEY not set; aborting smoke test", file=sys.stderr)
        return 2

    print("=" * 60)
    print("Smoke test: real cloud browser via browser-use")
    print("Target: https://example.com (canonical minimal page)")
    print("Schema: {title, heading, paragraph}")
    print("=" * 60)

    result = await browser_use_task.ainvoke(
        {
            "task": (
                "Go to https://example.com. Extract the exact text of the "
                "page <title>, the <h1> heading, and the first <p> paragraph."
            ),
            "max_steps": 5,
            "flash_mode": True,
            "output_schema": {
                "title": "the <title> element text",
                "heading": "the <h1> heading text",
                "paragraph": "the first <p> paragraph text",
            },
        }
    )

    print()
    print("=" * 60)
    print("RESULT")
    print("=" * 60)
    print(f"success:    {result.success}")
    print(f"steps:      {result.steps_taken}")
    print(f"urls:       {result.urls_visited}")
    print(f"final:      {result.final_result[:200]!r}")
    print(f"extracted:  {json.dumps(result.extracted, indent=2, ensure_ascii=False)}")
    if result.error:
        print(f"error:      {result.error}")

    # Sanity-check the structured extraction against the known page content
    expected = {"title": "Example Domain", "heading": "Example Domain"}
    for key, want in expected.items():
        got = result.extracted.get(key, "")
        if want.lower() not in str(got).lower():
            print(f"FAIL: expected {key!r} to contain {want!r}, got {got!r}", file=sys.stderr)
            return 1
    print()
    print("PASS: extracted title/heading match Example Domain")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

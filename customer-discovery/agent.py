"""
Customer Discovery Agent

Monitors HN, Reddit, and Twitter/X for people expressing pain points
about AI agents. Analyzes posts with Claude, writes to Notion, and
sends high-signal alerts via Telegram.

Usage:
    python agent.py              # Run once (all sources)
    python agent.py --source hn  # Run HN only
    python agent.py --source reddit
    python agent.py --schedule   # Run on loop (every 4 hours)
"""

import os
import sys
import json
import time
import hashlib
import logging
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import anthropic
from notion_client import Client as NotionClient

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get(
    "NOTION_DATABASE_ID", "8c460c44-2062-488b-aa48-ce558976889a"
)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# How far back to look (hours)
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "24"))

# Minimum opportunity score to trigger a Telegram alert
ALERT_THRESHOLD = int(os.environ.get("ALERT_THRESHOLD", "7"))

# Run interval in seconds when using --schedule (default 4 hours)
RUN_INTERVAL = int(os.environ.get("RUN_INTERVAL", str(4 * 3600)))

# Search terms for HN
HN_SEARCH_TERMS = [
    "AI agent",
    "LLM agent",
    "autonomous agent",
    "agent framework",
    "agentic AI",
    "AI workflow",
    "agent tool use",
    "agent orchestration",
]

# Subreddits to monitor
REDDIT_SUBREDDITS = [
    "AutoGPT",
    "LangChain",
    "LocalLLaMA",
    "ChatGPT",
    "artificial",
    "MachineLearning",
    "SaaS",
]

# State file to track already-processed posts
STATE_DIR = Path(os.environ.get("STATE_DIR", "."))
STATE_FILE = STATE_DIR / "seen_posts.json"

log = logging.getLogger("discovery")

# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def load_seen() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen(seen: set[str]):
    STATE_FILE.write_text(json.dumps(list(seen)[-5000:]))  # keep last 5k


def post_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------


def scrape_hn(client: httpx.Client) -> list[dict]:
    """Search HN via the free Algolia API."""
    since = int((datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).timestamp())
    posts = []

    for term in HN_SEARCH_TERMS:
        try:
            resp = client.get(
                "https://hn.algolia.com/api/v1/search_by_date",
                params={
                    "query": term,
                    "tags": "(story,show_hn,ask_hn)",
                    "numericFilters": f"created_at_i>{since}",
                    "hitsPerPage": 30,
                },
                timeout=15,
            )
            resp.raise_for_status()
            for hit in resp.json().get("hits", []):
                url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
                posts.append(
                    {
                        "source": "Hacker News",
                        "title": hit.get("title", ""),
                        "url": url,
                        "author": hit.get("author", ""),
                        "text": (hit.get("story_text") or hit.get("comment_text") or "")[:2000],
                        "engagement": hit.get("points", 0),
                        "created": hit.get("created_at", ""),
                        "hn_id": hit.get("objectID", ""),
                    }
                )
        except Exception as e:
            log.warning(f"HN search failed for '{term}': {e}")

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for p in posts:
        if p["url"] not in seen_urls:
            seen_urls.add(p["url"])
            unique.append(p)
    return unique


def scrape_reddit(client: httpx.Client) -> list[dict]:
    """Scrape Reddit using the public JSON API (no auth needed)."""
    posts = []

    for sub in REDDIT_SUBREDDITS:
        try:
            resp = client.get(
                f"https://www.reddit.com/r/{sub}/new.json",
                params={"limit": 50, "t": "day"},
                headers={"User-Agent": "CustomerDiscoveryBot/1.0"},
                timeout=15,
            )
            resp.raise_for_status()
            since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

            for child in resp.json().get("data", {}).get("children", []):
                data = child.get("data", {})
                created = datetime.fromtimestamp(data.get("created_utc", 0), tz=timezone.utc)
                if created < since:
                    continue

                posts.append(
                    {
                        "source": "Reddit",
                        "title": data.get("title", ""),
                        "url": f"https://reddit.com{data.get('permalink', '')}",
                        "author": data.get("author", ""),
                        "text": (data.get("selftext") or "")[:2000],
                        "engagement": data.get("score", 0) + data.get("num_comments", 0),
                        "created": created.isoformat(),
                        "subreddit": sub,
                    }
                )
        except Exception as e:
            log.warning(f"Reddit scrape failed for r/{sub}: {e}")

    return posts


# ---------------------------------------------------------------------------
# Claude Analysis
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = """\
You are a customer discovery analyst for a startup building in the AI agent space.

Analyze the following batch of posts from online forums. For EACH post, determine:
1. Is this person expressing a real pain point, frustration, or unmet need related to AI agents? (not just general discussion/hype)
2. If yes, categorize the pain point into one or more of: Memory/State, Reliability, Cost/Pricing, UX/Onboarding, Integration, Performance, Security, Missing Feature, Other
3. Rate the opportunity score (1-10) based on:
   - How acute is the pain? (frustrated vs. mildly annoyed)
   - How many people likely share this problem?
   - Could a product solve this?
4. Extract the most quotable snippet (1-2 sentences max)
5. Write a one-sentence summary of the pain point

Return a JSON array. For posts that are NOT real pain points (just news, hype, or general discussion), return null for that entry. Only include posts with genuine frustration or unmet needs.

Format:
[
  {
    "index": 0,
    "is_pain_point": true,
    "categories": ["Reliability", "Missing Feature"],
    "opportunity_score": 8,
    "key_quote": "I've tried 4 frameworks and none handle long-running state...",
    "summary": "Developer frustrated with lack of persistent state management across agent sessions"
  },
  {
    "index": 1,
    "is_pain_point": false
  },
  ...
]

Posts to analyze:
"""


def analyze_posts(posts: list[dict]) -> list[dict]:
    """Send posts to Claude for pain-point analysis. Process in batches."""
    if not posts:
        return []

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    results = []
    batch_size = 15  # keep under context limits

    for i in range(0, len(posts), batch_size):
        batch = posts[i : i + batch_size]
        posts_text = ""
        for j, p in enumerate(batch):
            posts_text += f"\n--- Post {j} ---\n"
            posts_text += f"Source: {p['source']}\n"
            posts_text += f"Title: {p['title']}\n"
            posts_text += f"Author: {p['author']}\n"
            posts_text += f"Engagement: {p['engagement']}\n"
            if p.get("text"):
                posts_text += f"Body: {p['text'][:1000]}\n"

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                messages=[
                    {"role": "user", "content": ANALYSIS_PROMPT + posts_text}
                ],
            )
            text = response.content[0].text

            # Extract JSON from response
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                analyses = json.loads(text[start:end])
            else:
                log.warning("Could not parse Claude response as JSON")
                continue

            for analysis in analyses:
                if analysis and analysis.get("is_pain_point"):
                    idx = analysis["index"]
                    if idx < len(batch):
                        post = batch[idx]
                        results.append({**post, **analysis})

        except Exception as e:
            log.error(f"Claude analysis failed: {e}")

    return results


# ---------------------------------------------------------------------------
# Notion Writer
# ---------------------------------------------------------------------------


def write_to_notion(results: list[dict], seen: set[str]):
    """Write pain-point results to the Notion database."""
    if not results or not NOTION_API_KEY:
        return

    notion = NotionClient(auth=NOTION_API_KEY)
    written = 0

    for r in results:
        pid = post_id(r["url"])
        if pid in seen:
            continue

        categories = r.get("categories", [])

        properties: dict = {
            "Post Title": {"title": [{"text": {"content": r["title"][:200]}}]},
            "Source": {"select": {"name": r["source"]}},
            "URL": {"url": r["url"]},
            "Author": {"rich_text": [{"text": {"content": r.get("author", "")[:100]}}]},
            "Opportunity Score": {"number": r.get("opportunity_score", 0)},
            "Key Quote": {
                "rich_text": [{"text": {"content": r.get("key_quote", "")[:2000]}}]
            },
            "Summary": {
                "rich_text": [{"text": {"content": r.get("summary", "")[:2000]}}]
            },
            "Engagement": {"number": r.get("engagement", 0)},
            "Pain Point Category": {
                "multi_select": [{"name": c} for c in categories if c]
            },
        }

        try:
            notion.pages.create(
                parent={"database_id": NOTION_DATABASE_ID},
                properties=properties,
            )
            seen.add(pid)
            written += 1
        except Exception as e:
            log.error(f"Notion write failed for '{r['title'][:50]}': {e}")

    log.info(f"Wrote {written} new entries to Notion")


# ---------------------------------------------------------------------------
# Telegram Alerts
# ---------------------------------------------------------------------------


def send_telegram(text: str):
    """Send a message via Telegram bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured, skipping alert")
        return

    with httpx.Client() as client:
        try:
            client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
        except Exception as e:
            log.error(f"Telegram send failed: {e}")


def send_alerts(results: list[dict], seen_before: set[str]):
    """Send Telegram alerts for high-signal posts."""
    high_signal = [
        r
        for r in results
        if r.get("opportunity_score", 0) >= ALERT_THRESHOLD
        and post_id(r["url"]) not in seen_before
    ]

    if not high_signal:
        return

    for r in high_signal:
        categories = ", ".join(r.get("categories", []))
        msg = (
            f"<b>Pain Point Found ({r['source']})</b>\n"
            f"Score: {r.get('opportunity_score', '?')}/10\n"
            f"Category: {categories}\n\n"
            f"<b>{r['title'][:200]}</b>\n"
            f"<i>\"{r.get('key_quote', 'N/A')[:300]}\"</i>\n\n"
            f"{r.get('summary', '')[:200]}\n\n"
            f"<a href=\"{r['url']}\">View post</a> | "
            f"Engagement: {r.get('engagement', 0)}"
        )
        send_telegram(msg)
        time.sleep(0.5)  # rate limit

    # Daily digest summary
    if len(high_signal) > 1:
        category_counts: dict[str, int] = {}
        for r in high_signal:
            for c in r.get("categories", []):
                category_counts[c] = category_counts.get(c, 0) + 1

        top_categories = sorted(category_counts.items(), key=lambda x: -x[1])[:5]
        cat_lines = "\n".join(f"  {c}: {n}" for c, n in top_categories)

        summary = (
            f"<b>Discovery Summary</b>\n"
            f"Found {len(high_signal)} high-signal posts (score >= {ALERT_THRESHOLD})\n\n"
            f"<b>Top pain points:</b>\n{cat_lines}"
        )
        send_telegram(summary)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_discovery(sources: list[str] | None = None):
    """Run one cycle of customer discovery."""
    sources = sources or ["hn", "reddit"]
    seen = load_seen()
    seen_before = set(seen)  # snapshot for alert dedup

    all_posts: list[dict] = []

    with httpx.Client() as client:
        if "hn" in sources:
            log.info("Scraping Hacker News...")
            hn_posts = scrape_hn(client)
            # Filter already-seen
            hn_posts = [p for p in hn_posts if post_id(p["url"]) not in seen]
            log.info(f"  Found {len(hn_posts)} new HN posts")
            all_posts.extend(hn_posts)

        if "reddit" in sources:
            log.info("Scraping Reddit...")
            reddit_posts = scrape_reddit(client)
            reddit_posts = [p for p in reddit_posts if post_id(p["url"]) not in seen]
            log.info(f"  Found {len(reddit_posts)} new Reddit posts")
            all_posts.extend(reddit_posts)

    if not all_posts:
        log.info("No new posts to analyze")
        return

    log.info(f"Analyzing {len(all_posts)} posts with Claude...")
    results = analyze_posts(all_posts)
    log.info(f"  Found {len(results)} pain points")

    if results:
        log.info("Writing to Notion...")
        write_to_notion(results, seen)

        log.info("Sending Telegram alerts...")
        send_alerts(results, seen_before)

    # Mark all scraped posts as seen (even non-pain-points)
    for p in all_posts:
        seen.add(post_id(p["url"]))
    save_seen(seen)

    log.info("Done")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Customer Discovery Agent")
    parser.add_argument(
        "--source",
        choices=["hn", "reddit", "all"],
        default="all",
        help="Which source to scrape (default: all)",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help=f"Run on a loop every {RUN_INTERVAL}s",
    )
    args = parser.parse_args()

    sources = ["hn", "reddit"] if args.source == "all" else [args.source]

    # Validate config
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not NOTION_API_KEY:
        missing.append("NOTION_API_KEY")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        log.warning(f"Missing env vars (some features disabled): {', '.join(missing)}")

    if args.schedule:
        log.info(f"Running on schedule (every {RUN_INTERVAL}s)")
        while True:
            try:
                run_discovery(sources)
            except Exception as e:
                log.error(f"Run failed: {e}", exc_info=True)
            log.info(f"Sleeping {RUN_INTERVAL}s until next run...")
            time.sleep(RUN_INTERVAL)
    else:
        run_discovery(sources)


if __name__ == "__main__":
    main()

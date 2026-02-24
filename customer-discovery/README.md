# Customer Discovery Agent

Monitors Hacker News and Reddit for people expressing pain points about AI agents. Analyzes posts with Claude, writes results to Notion, and sends high-signal alerts to Telegram.

## How it works

1. **Scrape** HN (Algolia API) and Reddit (public JSON) for AI agent discussions
2. **Filter** with Claude — only surfaces posts with real frustration/unmet needs, not hype
3. **Write** results to your [Notion database](https://www.notion.so/8c460c442062488baa48ce558976889a) with pain point categories and opportunity scores
4. **Alert** via Telegram for posts scoring 7+/10

## Setup

### 1. Get your Telegram chat ID

Message [@userinfobot](https://t.me/userinfobot) on Telegram — it replies with your chat ID.

### 2. Create a Notion integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Create a new integration, copy the API key
3. Share the "Customer Discovery Agent" database with your integration

### 3. Set environment variables

Copy `.env.example` and fill in your keys:

```
ANTHROPIC_API_KEY=sk-ant-...
NOTION_API_KEY=ntn_...
TELEGRAM_BOT_TOKEN=<same token as your OpenClaw bot>
TELEGRAM_CHAT_ID=<your chat ID from step 1>
```

### 4. Deploy on Railway

**Option A: Separate Railway service** (recommended)

1. In your Railway project, add a new service
2. Point it at this `customer-discovery/` directory
3. Add the env vars above
4. It will auto-run on a 4-hour loop via `--schedule`

**Option B: Run locally / one-shot**

```bash
cd customer-discovery
pip install -r requirements.txt
python agent.py              # run once
python agent.py --source hn  # HN only
python agent.py --schedule   # loop every 4h
```

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `LOOKBACK_HOURS` | 24 | How far back to search |
| `ALERT_THRESHOLD` | 7 | Min score to trigger Telegram alert |
| `RUN_INTERVAL` | 14400 | Seconds between runs in schedule mode |
| `STATE_DIR` | `.` | Where to store `seen_posts.json` for dedup |

## Notion database schema

The database is already created with these columns:
- **Post Title** (title)
- **Source** (select: Reddit, Hacker News, Twitter/X)
- **Pain Point Category** (multi-select: Memory/State, Reliability, Cost/Pricing, UX/Onboarding, Integration, Performance, Security, Missing Feature, Other)
- **Opportunity Score** (number, 1-10)
- **Key Quote** (text)
- **Summary** (text)
- **Engagement** (number)
- **Author** (text)
- **URL** (url)

## Twitter/X

Not included by default since it requires a paid API ($100/mo basic tier). Options:
- **Twitter API v2** — add `TWITTER_BEARER_TOKEN` and extend `agent.py`
- **Apify Twitter scraper** — ~$5/1000 tweets, no official API needed
- **Nitter RSS** — free but unreliable

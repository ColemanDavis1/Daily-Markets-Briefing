# Morning Briefing

Automated daily business and financial intelligence email, delivered at 9:30 AM weekdays.

**What it does:** Aggregates live headlines from Reuters, CNBC, MarketWatch, Yahoo Finance, WSJ, and SEC EDGAR; pulls real-time market data via Yahoo Finance; synthesizes everything through Claude into a structured briefing; renders a premium HTML email; and delivers it via SendGrid (or SMTP fallback).

---

## Project Structure

```
daily-briefing/
├── config.py             # Central config loaded from environment variables
├── news_aggregator.py    # RSS feeds, yfinance market data, SEC EDGAR
├── ai_synthesizer.py     # Claude API synthesis → structured JSON
├── email_renderer.py     # Jinja2 HTML rendering
├── email_sender.py       # SendGrid (primary) + SMTP (fallback) delivery
├── scheduler.py          # APScheduler Mon–Fri 9:30 AM with retry logic
├── main.py               # Pipeline orchestrator + CLI entry point
├── templates/
│   └── briefing.html     # Jinja2 HTML email template
├── requirements.txt
├── .env.example          # Environment variable template
└── briefing_log.json     # Auto-created; logs every run
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)
- A [SendGrid account](https://sendgrid.com/) (free tier: 100 emails/day) **or** SMTP credentials

### 2. Clone and install

```bash
git clone https://github.com/your-org/daily-briefing.git
cd daily-briefing

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in at minimum:

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `SENDGRID_API_KEY` | Yes* | SendGrid API key (*or use SMTP) |
| `SENDER_EMAIL` | Yes | Verified sender address |
| `RECIPIENT_EMAILS` | Yes | Comma-separated recipient list |
| `ADMIN_EMAIL` | Recommended | Alert destination on pipeline failure |
| `TIMEZONE` | No | Default: `America/New_York` |
| `SMTP_HOST/USER/PASSWORD` | No | SMTP fallback credentials |

### 4. Test locally (no email sent)

```bash
python main.py --dry-run
```

This runs the full pipeline and saves the rendered email to `briefing_preview.html`. Open it in a browser to verify the output before enabling delivery.

### 5. Send a live test email

```bash
python main.py
```

### 6. Start the scheduler

```bash
python scheduler.py
```

The process runs continuously and triggers the pipeline at the configured time each weekday.

---

## Deployment

### Option A: Railway.app (recommended for simplicity)

Railway's free tier is sufficient for this workload.

1. Push the project to a GitHub repository.
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**.
3. Set all environment variables in the Railway dashboard under **Variables**.
4. In **Settings → Deploy**, set the start command:
   ```
   python scheduler.py
   ```
5. Deploy. Railway keeps the process alive and restarts it on crash.

**No Procfile required** — Railway detects the start command from settings.

---

### Option B: Linux VPS with systemd

Replace `/opt/daily-briefing` with your actual install path.

**1. Install the app:**

```bash
sudo mkdir -p /opt/daily-briefing
sudo chown $USER:$USER /opt/daily-briefing
git clone https://github.com/your-org/daily-briefing.git /opt/daily-briefing
cd /opt/daily-briefing
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# Edit .env with your values
```

**2. Create a systemd service:**

```ini
# /etc/systemd/system/morning-briefing.service
[Unit]
Description=Morning Briefing Scheduler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/opt/daily-briefing
EnvironmentFile=/opt/daily-briefing/.env
ExecStart=/opt/daily-briefing/.venv/bin/python scheduler.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**3. Enable and start:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable morning-briefing
sudo systemctl start morning-briefing
sudo journalctl -u morning-briefing -f    # tail logs
```

---

### Option C: GitHub Actions (lightweight, no persistent server)

Use this if you don't have a server and want GitHub to trigger the run.

**Note:** GitHub Actions has no persistent process — it wakes up, runs the pipeline, and exits. There is no retry between runs (only within a run). Suitable for low-criticality use cases.

Create `.github/workflows/morning-briefing.yml`:

```yaml
name: Morning Briefing

on:
  schedule:
    # 9:30 AM ET = 13:30 UTC (adjust for DST manually, or use a timezone-aware cron service)
    - cron: '30 13 * * 1-5'
  workflow_dispatch:    # allows manual trigger from the Actions tab

jobs:
  send-briefing:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run pipeline
        env:
          GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}
          SENDGRID_API_KEY: ${{ secrets.SENDGRID_API_KEY }}
          SENDER_EMAIL: ${{ secrets.SENDER_EMAIL }}
          RECIPIENT_EMAILS: ${{ secrets.RECIPIENT_EMAILS }}
          ADMIN_EMAIL: ${{ secrets.ADMIN_EMAIL }}
          TIMEZONE: America/New_York
        run: python main.py
```

Add all secrets in **GitHub repo → Settings → Secrets and variables → Actions**.

**Limitation:** GitHub Actions cron has ~1–5 min jitter and pauses on inactive repos. For production use, prefer Railway or a VPS.

---

## Monitoring

Every pipeline run appends a structured entry to `briefing_log.json`:

```json
{
  "timestamp": "2025-05-21T09:30:12.345Z",
  "dry_run": false,
  "status": "success",
  "sections_generated": ["top_story", "markets_macro", "corporate_intelligence", ...],
  "sources_used": ["reuters_business", "cnbc_markets", "yfinance", ...],
  "sources_failed": [],
  "delivery": {"method": "sendgrid", "success": true, "recipients": [...]},
  "error": null
}
```

Status values: `success`, `delivery_failed`, `failed`, `config_error`, `dry_run_complete`.

The log retains the last 180 entries. On Railway/VPS, the log is ephemeral unless you mount persistent storage or ship logs to an external service (e.g., Datadog, Logtail).

---

## Customization

| What to change | Where |
|---|---|
| Delivery time | `SCHEDULE_HOUR` / `SCHEDULE_MINUTE` in `.env` |
| AI model | `ANTHROPIC_MODEL` in `.env` |
| Add/remove news sources | `RSS_FEEDS` dict in `news_aggregator.py` |
| Email tone/style | System prompt in `ai_synthesizer.py` |
| Template design | `templates/briefing.html` |
| Recipient list | `RECIPIENT_EMAILS` in `.env` (comma-separated) |

---

## Troubleshooting

**`ANTHROPIC_API_KEY` not set** → Copy `.env.example` to `.env` and fill in all required values.

**SendGrid 403 error** → Verify your sender address in the SendGrid dashboard (Settings → Sender Authentication).

**Market data shows N/A** → yfinance may be rate-limited. The run will still complete — Claude will note missing data in the briefing.

**RSS feeds returning empty** → Some feeds (FT, WSJ) may block scraping from cloud IPs. Add a custom `User-Agent` or replace the feed URL with an alternative. The pipeline skips failed sources and logs them.

**Template renders incorrectly in Outlook** → Outlook uses Word's HTML renderer. The template uses table-based layout for maximum compatibility, but test with [Litmus](https://litmus.com) or [Email on Acid](https://www.emailonacid.com) for full client coverage.

---

## Data Sources

| Source | Method | Notes |
|---|---|---|
| Reuters | RSS | Business and markets feeds |
| CNBC | RSS | Markets and finance feeds |
| MarketWatch | RSS | Top stories and market pulse |
| Yahoo Finance | RSS + yfinance | Headlines + real-time market data |
| WSJ | RSS | Markets feed (some content gated) |
| Barron's | RSS | Market data feed |
| Federal Reserve | RSS | All press releases |
| Seeking Alpha | RSS | Market currents |
| SEC EDGAR | REST API | Same-day 8-K filings |
| yfinance | Library | S&P 500, NASDAQ, Dow, 10Y Treasury, DXY, WTI, Gold, BTC |

Market data is sourced directly from Yahoo Finance via yfinance and is not processed by the AI model. The AI synthesizes only the news narrative sections.

---

## License

MIT

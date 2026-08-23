# The Morning Desk

A daily markets newsletter built for investment banking interview preparation.
It lands at 7:00 AM ET on weekdays with the broader market picture, a full
rates, Fed and funding section, and one explained story from each of six
coverage groups and six product groups.

The design goal is not headlines. It is being able to answer "what's going on in
the markets?" and "walk me through a deal you've been following" with specific
numbers and the reasoning behind them.

## What is in an issue

| Block | Content |
|---|---|
| The one thing | A single quotable sentence answering "what's going on in the markets?" |
| Know these cold | Index levels, 10Y, 2Y, 2s10s, SOFR, EFFR, Fed target, next FOMC, priced policy path, CPI, Core PCE, unemployment, HY OAS, WTI, gold, dollar |
| The Lead | The day's dominant story and why it outranks everything else |
| The tape | Prior close, overnight futures, sector rotation bars, cross-asset, overseas |
| The curve | Full 3M to 30Y curve with daily bp changes, 2s10s, 3M10Y, 5s30s |
| Rates, the Fed & Funding | The longest section. Curve shape, SOFR against IORB, reverse repo, breakevens against real yields, credit spreads, the implied policy path |
| Economic Data | Latest prints against consensus and prior, with the Fed read-through |
| Coverage Groups | TMT, Healthcare, Industrials, Consumer & Retail, Energy & Power, FIG |
| Product Groups | M&A, ECM, DCM, Leveraged Finance, Sponsors, Restructuring |
| Geopolitics & Policy | One development with a concrete market transmission channel |
| What to Watch | Four or five catalysts in the next 24 to 72 hours with bull and bear cases |
| Calendars | Economic releases, earnings this week, fresh 8-K filings |

Every desk story ends with an **If they ask** box: the question an interviewer
would put to you off that story, and a spoken-word answer you could deliver
verbatim.

## Architecture

The pipeline separates numbers from prose, which is the accuracy guarantee.

```
news_aggregator.py   pulls prices, FRED series, news, calendars, filings
market_engine.py     computes EVERY number and a rule-based reading of it
ai_synthesizer.py    writes the prose, given the computed numbers as fact
claude_client.py     runs generation on the CLI (subscription) or the API
email_renderer.py    builds the Jinja context, derives subject and preheader
templates/           the newsletter itself
main.py              orchestration
```

`market_engine.py` produces a **verified facts block** that is injected into
every model call. The model is told it may explain those figures but may not
introduce a number that is not either in that block or stated in the day's
source articles. A second verification pass then re-checks each draft against
the same block and strips anything unsupported.

Consequence: if a figure appears in the email, it was pulled from a primary
source or arithmetically derived from one. Curve spreads, funding spreads,
percentile ranks, the implied policy path and the entire Know These Cold block
are computed in Python, never generated.

## Cost

Zero marginal cost, by design.

| Component | Cost |
|---|---|
| Claude, via the CLI on a Pro or Max subscription | $0 |
| FRED, Yahoo, Finnhub, SEC EDGAR, RSS | $0 |
| GitHub Actions, roughly 5 minutes a day | $0, well inside the free tier |
| SendGrid or Gmail SMTP, one email a day | $0 |

Set `LLM_BACKEND=api` to run on metered credits instead. On Sonnet that is
roughly $1.50 to $3.00 a run with verification on.

## Setup

### 1. Install and configure

```bash
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`. The only strictly required data key is `FRED_API_KEY`, a free
32-character key from [fredaccount.stlouisfed.org](https://fredaccount.stlouisfed.org/apikeys).
`FINNHUB_API_KEY` is strongly recommended for the calendars.

### 2. Authenticate the model backend

For local runs, log the CLI in once:

```bash
claude login
```

The pipeline reports a clear error if that session expires, and falls back to a
deterministic edition rather than sending nothing.

### 3. Preview before sending

```bash
python main.py --dry-run
```

Writes `briefing_preview.html`. Open it in a browser. Add `--data-only` to
render the numbers with no model calls at all, which is the fast way to check
data health.

### 4. Send

```bash
python main.py
```

## Running in GitHub Actions

Two workflows: `prepare.yml` writes the issue at 6:40 AM ET and uploads it as an
artifact; `morning-briefing.yml` sends it at 7:00 AM ET. Splitting them means a
slow or failed write never delays the send, and the send job can retry against
the last good artifact.

GitHub cron is UTC and does not observe daylight saving, so each workflow
registers both candidate UTC times and a guard step exits the run that is not
actually the right hour in New York.

### Secrets to set

| Secret | Purpose |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Subscription-billed generation in CI. Generate with `claude setup-token`. |
| `FRED_API_KEY` | Required. |
| `FINNHUB_API_KEY` | Calendars and merger news. |
| `NEWS_API_KEY` | Optional. |
| `SENDER_EMAIL`, `RECIPIENT_EMAILS`, `ADMIN_EMAIL` | Addresses. |
| `SMTP_PASSWORD` or `SENDGRID_API_KEY` | Delivery. |
| `ANTHROPIC_API_KEY` | Optional metered fallback. |

## Degradation

Nothing in the pipeline is allowed to block the morning.

- A dead news source logs and skips.
- A missing FRED series drops its row; the rest of the section still renders.
- An implausible price move (a stale baseline, a split, a bad bar) has its
  change suppressed and the level kept, rather than publishing a wrong number.
- A failed model call falls back to that desk's real headlines as links.
- No model backend at all ships the full deterministic edition, clearly banner-labeled.
- A quiet desk says so and gives standing context instead of inventing a story.

## Maintenance

RSS feeds rot quietly. Audit them:

```bash
python tools/feed_check.py
```

This flags feeds that return nothing and feeds still responding with stale
content. Reuters, FT and Treasury have retired their public RSS; WSJ and
MarketWatch marketpulse respond but serve frozen 2025 content. All were removed
for that reason.

Two things need a calendar check:

- **FOMC dates** are hardcoded in `market_engine.FOMC_DECISION_DATES`. Update
  annually from federalreserve.gov when the next year's schedule publishes.
- **Stooq** is now a fallback only. It began returning an HTML block page
  instead of CSV, so Yahoo is primary for all prices.

## A note on the implied policy path

Cut and hike expectations are derived from the Treasury curve, not from fed
funds futures. The 1-year yield approximates the expected average overnight rate
over the coming year; doubling the gap to the current effective funds rate
approximates the cumulative move under a gradual path. Term premium is ignored.

These figures will differ modestly from CME FedWatch, which is the number an
interviewer is most likely to have in mind. The email states the methodology so
the difference can be caveated out loud. Treat it as directionally right rather
than precise.

## Not investment advice

Built for interview preparation. Not investment advice and not a recommendation
to buy or sell any security.

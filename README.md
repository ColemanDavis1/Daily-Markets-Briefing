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

### Making sure it never bills you

There is no API key involved in the subscription path. Locally, `claude login`
authenticates the CLI with your Claude account and the pipeline shells out to
it; nothing is metered. In CI, `claude setup-token` mints a long-lived OAuth
token tied to the same subscription.

Three guards keep a run from quietly moving to paid credits:

1. `LLM_ALLOW_API_FALLBACK` defaults to **false**. While it is false the metered
   backend is never used, even when an `ANTHROPIC_API_KEY` is present in the
   environment and even when the CLI is unreachable.
2. `claude_client.py` strips `ANTHROPIC_API_KEY` from the environment it hands
   to the CLI, so a stray key cannot divert a subscription call.
3. The workflows pass no API key at all.

If subscription auth fails, the run ships the deterministic edition rather than
billing you. To verify a run stayed free, check the log: each call prints the
cost the CLI reports, which is `$0.0000` on subscription auth, and a non-zero
figure raises a warning.

Setting `LLM_ALLOW_API_FALLBACK=true` with a key opts into metered billing. On
Sonnet that is roughly $1.50 to $3.00 a run with verification on.

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

### How Claude authenticates in CI

GitHub Actions runs unattended, so there is no browser and no interactive login.
The CLI handles this with a long-lived OAuth token tied to your subscription.

Run this once, locally, on a machine where you are already logged in:

```bash
claude setup-token
```

It opens a browser, confirms your account, and prints a token. Store that token
as the `CLAUDE_CODE_OAUTH_TOKEN` repository secret. The workflow installs the
CLI and sets that variable, and the CLI then authenticates headlessly as your
subscription. No API key, no metered spend.

The three systems are doing separate jobs and none of them needs the others'
credentials:

| System | Job | Credential |
|---|---|---|
| GitHub Actions | Runs the cron and executes the pipeline | Repository secrets |
| Claude CLI | Writes the desk stories inside the prepare job | `CLAUDE_CODE_OAUTH_TOKEN` |
| SendGrid or Gmail SMTP | Delivers the finished HTML | `SENDGRID_API_KEY` or `SMTP_PASSWORD` |

Caveats worth knowing:

- The token is long-lived but not permanent. It can expire or be revoked. When
  that happens the run ships the deterministic edition and the log states the
  auth failure, so re-mint with `claude setup-token` and update the secret.
- CI usage counts against your plan's normal limits, same as local usage.
- Subscription auth in CI is a comparatively new capability. If it stops working,
  the fully local option below needs no token at all.

### Running it locally on a schedule instead

If you would rather not manage a token, skip GitHub Actions and let your own
machine do it. The CLI is already logged in there, so nothing extra is needed.

Create a Windows scheduled task that runs at 6:40 AM on weekdays:

```powershell
schtasks /create /tn "Morning Desk" /tr "cmd /c cd /d \"%USERPROFILE%\OneDrive - University of North Carolina at Chapel Hill\AI\Claude Code\Daily-News-Briefing\" && .venv\Scripts\python.exe main.py" /sc weekly /d MON,TUE,WED,THU,FRI /st 06:40
```

Tradeoff: your machine has to be awake at that hour. GitHub Actions does not
care whether your laptop is open.

### Secrets to set

| Secret | Purpose |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Subscription-billed generation in CI. Generate with `claude setup-token`. |
| `FRED_API_KEY` | Required. |
| `FINNHUB_API_KEY` | Calendars and merger news. |
| `NEWS_API_KEY` | Optional. |
| `SENDER_EMAIL`, `RECIPIENT_EMAILS`, `ADMIN_EMAIL` | Addresses. |
| `SMTP_PASSWORD` or `SENDGRID_API_KEY` | Delivery. |
| `ANTHROPIC_API_KEY` | Not needed. Only used if you deliberately set `LLM_ALLOW_API_FALLBACK=true`. |

## How prices are made accurate

Getting index levels right turned out to be the subtlest part of the project,
because the data sources are fine and the arithmetic is where it goes wrong.

**Bars are read by date, never by value.** Yahoo's `meta.previousClose` is
consistently null and `meta.chartPreviousClose` is the close *before* the
requested range, so pairing it with `regularMarketPrice` reports a multi-week
move as a daily change. An earlier fix compared the live price to the last bar
by value to pick a baseline, which still broke twice a day: after the close it
could pair a live price against the very session it came from, and pre-market a
placeholder bar carrying the prior close could be compared against itself and
print 0.00%.

The rule now:

| Asset class | What it reports |
|---|---|
| Cash indices, sector ETFs, international | Last **completed** session's close against the session before it |
| Futures, FX, commodities, crypto | Live print against the last completed close |

A bar dated today only counts as completed after 16:15 ET, so a partial
mid-session bar is never mistaken for a close.

**Headline indices are cross-checked.** Before rendering, the S&P 500,
NASDAQ 100, Dow and VIX are compared against FRED's official daily close
series, matched on the specific session date. A disagreement above 0.05% is
logged and FRED wins, because FRED is the authoritative publisher and Yahoo is
a convenience. The log line for each index states whether it verified.

**Implausible moves are suppressed, not published.** A session move beyond a
per-asset-class bound (12% for an index, 6% for FX, 40% for crypto) is treated
as a data artifact: the level is kept and the change is dropped, because a
missing number is recoverable in an interview and a wrong one is not.

## Scheduling reality

GitHub's scheduler is best-effort and, on this repository, genuinely
unreliable. Observed behaviour: a cron firing ten hours late, another three to
four hours late, and a weekday where it never fired at all.

The design compensates rather than pretending otherwise:

- **Three attempts per season.** Prepare tries 7:00, 7:15 and 7:30 ET; send
  tries 7:20, 7:35 and 7:50 ET.
- **The send job holds until 8:00 ET.** An early or on-time start waits and
  delivers on the hour; a late start proceeds immediately.
- **Dated marker artifacts make it idempotent.** `prepared-YYYY-MM-DD` stops
  the later prepare crons from rewriting the issue and burning three times the
  model allowance. `sent-YYYY-MM-DD` stops a second email.

### The external dispatcher

Something outside this repository sends a `repository_dispatch` every day at
13:30 and 13:45 UTC, to the second. It is far more reliable than GitHub cron
and has been the trigger that actually delivered most issues.

If you want delivery pinned to 8:00 AM ET, retime that scheduler:

| Dispatch type | Set to (UTC) | Eastern |
|---|---|---|
| `trigger-prepare` | 11:30 | 7:30 AM |
| `trigger-send` | 12:00 | 8:00 AM |

If the service supports timezone-aware scheduling, set it to 7:30 and 8:00
`America/New_York` instead and it will handle the November DST change on its
own. Otherwise both need a one-hour shift when New York moves to EST.

The marker artifacts mean the dispatcher and the crons can safely overlap: only
the first trigger through does the work.

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

# Website Health Monitor — Mattpom Digital Ventures

Automated daily health crawler for five content sites. Runs 21 checks, emails a report, and uploads artifacts via GitHub Actions.

## Sites Monitored
- https://brokemodelife.com
- https://dontbehangry.com
- https://finelivingguide.com
- https://stoplookaround.com
- https://illaskforit.com

## Checks (21)
| # | Check | Severity |
|---|-------|----------|
| 1 | Site down / homepage unreachable | Critical |
| 2 | HTTP 4xx errors | Critical |
| 3 | HTTP 5xx errors | Critical |
| 4 | Broken internal links | Critical |
| 5 | Broken external links | Warning |
| 6 | Broken image URLs | Warning |
| 7 | Etsy affiliate links detected | Info |
| 8 | Amazon Associates links detected | Info |
| 9 | Amazon links missing `brokemodelife-20` tag | **Critical** |
| 10 | Pinterest links detected | Info |
| 11 | X/Twitter links detected | Info |
| 12 | Instagram links detected | Info |
| 13 | Booking.com links detected | Info |
| 14 | GetYourGuide links detected | Info |
| 15 | Redirect chains (3+ hops) | Warning |
| 16 | Slow pages (>5s) | Warning |
| 17 | robots.txt missing | Warning |
| 18 | sitemap.xml missing | Warning |
| 19 | Missing page title | Warning |
| 20 | Missing meta description | Warning |
| 21 | Missing canonical URL | Warning |

## Required GitHub Secrets
| Secret | Description |
|--------|-------------|
| `SMTP_HOST` | Brevo SMTP host (e.g. `smtp-relay.brevo.com`) |
| `SMTP_PORT` | Usually `587` |
| `SMTP_USER` | Brevo SMTP login (your email) |
| `SMTP_PASSWORD` | Brevo SMTP key |
| `SMTP_FROM` | From address |
| `MAKE_WEBHOOK_URL` | *(Optional)* Make.com webhook URL |

## Manual Run
1. Go to **Actions → Website Health Monitor → Run workflow**
2. Select mode: `daily`, `weekly`, or `monthly`
3. Click **Run workflow**

## Report Artifacts
After each run: **Actions → the run → Artifacts → website-health-reports**

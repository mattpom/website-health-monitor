# Website Health Monitor

Automated daily crawler for Mattpom Digital Ventures properties. Checks for broken links, missing SEO tags, affiliate link issues, and site availability. Reports by email and optional Make.com webhook.

## Sites Monitored

| Site | URL |
|------|-----|
| BrokeModeLife | https://brokemodelife.com |
| DontBeHangry | https://dontbehangry.com |
| FineLivingGuide | https://finelivingguide.com |
| StopLookAround | https://stoplookaround.com |
| IllAskForIt | https://illaskforit.com |

## Checks Performed

- ✅ Site down / homepage unreachable
- ✅ HTTP 4xx and 5xx errors
- ✅ Broken internal and external links
- ✅ Broken image URLs
- ✅ Amazon links missing `brokemodelife-20` tag
- ✅ Etsy, Pinterest, X/Twitter, Instagram links
- ✅ Booking.com and GetYourGuide links
- ✅ Redirect chains
- ✅ Slow pages (>3s)
- ✅ robots.txt availability
- ✅ sitemap.xml availability
- ✅ Missing page title
- ✅ Missing meta description
- ✅ Missing canonical URL

## GitHub Secrets Required

Set these in **Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `SMTP_HOST` | SMTP server hostname (e.g. `smtp.brevo.com`) |
| `SMTP_PORT` | SMTP port (e.g. `587`) |
| `SMTP_USER` | SMTP username / login |
| `SMTP_PASSWORD` | SMTP password or API key |
| `SMTP_FROM` | From email address |
| `MAKE_WEBHOOK_URL` | *(Optional)* Make.com webhook URL |

## Schedule

| Run | Time |
|-----|------|
| Daily | 7:00 AM ET (11:00 UTC) |
| Weekly summary | Sunday 8:00 AM ET |
| Monthly summary | 1st of month 8:30 AM ET |

## Manual Run

1. Go to **Actions** → **Website Health Monitor**
2. Click **Run workflow**
3. Select mode: `daily`, `weekly`, or `monthly`
4. Click **Run workflow**

## Reports

After each run, find reports under **Actions → [run] → Artifacts → website-health-reports**. Contains JSON, HTML, and plain text versions.

## Local Run

```bash
pip install -r requirements.txt
python crawler.py --mode daily
```

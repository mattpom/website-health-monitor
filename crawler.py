#!/usr/bin/env python3
"""
Website Health Monitor — Mattpom Digital Ventures
Crawls 5 content sites, runs 21 checks, emails a report via Brevo SMTP.
"""

import argparse
import json
import os
import re
import smtplib
import time
from collections import defaultdict
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin, urlparse
import urllib.request
import urllib.error

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
SITES = [
    "https://brokemodelife.com",
    "https://dontbehangry.com",
    "https://finelivingguide.com",
    "https://stoplookaround.com",
    "https://illaskforit.com",
]

AMAZON_TAG = "brokemodelife-20"
AFFILIATE_DOMAINS = {
    "etsy": "etsy.com",
    "pinterest": "pinterest.com",
    "twitter_x": "twitter.com",
    "twitter_x2": "x.com",
    "instagram": "instagram.com",
    "booking": "booking.com",
    "getyourguide": "getyourguide.com",
    "amazon": "amazon.com",
}

SLOW_PAGE_THRESHOLD = 5.0  # seconds
MAX_PAGES_PER_SITE = 50
REQUEST_TIMEOUT = 15
CRAWL_DELAY = 0.5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; MattpomHealthBot/1.0; "
        "+https://github.com/mattpom/website-health-monitor)"
    )
}

REPORT_DIR = Path("reports")
REPORT_DIR.mkdir(exist_ok=True)

EMAIL_TO = "mattpom63@gmail.com"


# ── Issue model ───────────────────────────────────────────────────────────────
def issue(severity, page_url, check, detail, fix):
    return {
        "severity": severity,   # critical | warning | info
        "page_url": page_url,
        "check": check,
        "detail": detail,
        "fix": fix,
    }


# ── HTTP helpers ──────────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update(HEADERS)


def fetch(url, timeout=REQUEST_TIMEOUT):
    """Return (response, elapsed_seconds) or (None, elapsed)."""
    t0 = time.time()
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        return r, time.time() - t0
    except Exception as exc:
        return None, time.time() - t0


def head_ok(url):
    try:
        r = session.head(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code == 405:
            r = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        return r.status_code, r.url
    except Exception:
        return None, url


# ── Per-page checks ───────────────────────────────────────────────────────────
def check_page(page_url, html, elapsed, issues):
    soup = BeautifulSoup(html, "html.parser")

    # 3. Missing page title
    title_tag = soup.find("title")
    if not title_tag or not title_tag.get_text(strip=True):
        issues.append(issue(
            "warning", page_url, "missing_title",
            "No <title> tag found.",
            "Add a descriptive <title> element to the <head>."
        ))

    # 4. Missing meta description
    meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if not meta_desc or not meta_desc.get("content", "").strip():
        issues.append(issue(
            "warning", page_url, "missing_meta_description",
            "No meta description tag found.",
            "Add <meta name='description' content='...'> to the <head>."
        ))

    # 5. Missing canonical URL
    canonical = soup.find("link", attrs={"rel": re.compile(r"canonical", re.I)})
    if not canonical or not canonical.get("href", "").strip():
        issues.append(issue(
            "warning", page_url, "missing_canonical",
            "No canonical <link> tag found.",
            "Add <link rel='canonical' href='...'> to the <head>."
        ))

    # 6. Slow page
    if elapsed > SLOW_PAGE_THRESHOLD:
        issues.append(issue(
            "warning", page_url, "slow_page",
            f"Page load time {elapsed:.1f}s exceeds {SLOW_PAGE_THRESHOLD}s threshold.",
            "Optimise images, enable caching, or use a CDN."
        ))

    # Collect all links
    all_links = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        full = urljoin(page_url, href)
        all_links.append(full)

    # 7. Affiliate link detection
    for link in all_links:
        parsed = urlparse(link)
        netloc = parsed.netloc.lower()

        # Amazon missing tag
        if "amazon.com" in netloc:
            if AMAZON_TAG not in link:
                issues.append(issue(
                    "critical", page_url, "amazon_missing_tag",
                    f"Amazon link missing '{AMAZON_TAG}' tag: {link}",
                    f"Add tag={AMAZON_TAG} parameter to the URL."
                ))
            else:
                issues.append(issue(
                    "info", page_url, "amazon_affiliate_link",
                    f"Amazon affiliate link detected: {link}",
                    "No action needed — tag present."
                ))

        for label, domain in AFFILIATE_DOMAINS.items():
            if domain in netloc and domain != "amazon.com":
                issues.append(issue(
                    "info", page_url, f"affiliate_link_{label}",
                    f"{label.title()} link detected: {link}",
                    "Verify link is still active and properly attributed."
                ))

    # 8. Redirect chains
    for link in all_links:
        try:
            resp = session.get(link, timeout=10, allow_redirects=True)
            if len(resp.history) >= 3:
                chain = " → ".join(
                    [r.url for r in resp.history] + [resp.url]
                )
                issues.append(issue(
                    "warning", page_url, "redirect_chain",
                    f"Redirect chain ({len(resp.history)} hops): {chain}",
                    "Update the link to point directly to the final URL."
                ))
        except Exception:
            pass

    # 9. Broken internal links
    base_domain = urlparse(page_url).netloc
    internal = [l for l in all_links if urlparse(l).netloc == base_domain]
    for link in internal[:30]:  # cap to avoid rate limits
        status, final = head_ok(link)
        if status is None:
            issues.append(issue(
                "critical", page_url, "broken_internal_link",
                f"Internal link unreachable: {link}",
                "Fix or remove the broken link."
            ))
        elif 400 <= status < 500:
            issues.append(issue(
                "critical", page_url, "http_4xx",
                f"Internal link returned {status}: {link}",
                "Fix the URL or add a redirect."
            ))
        elif 500 <= status < 600:
            issues.append(issue(
                "critical", page_url, "http_5xx",
                f"Internal link returned {status}: {link}",
                "Investigate server error at the destination."
            ))

    # 10. Broken external links (sampled)
    external = [l for l in all_links if urlparse(l).netloc != base_domain]
    for link in external[:20]:
        status, _ = head_ok(link)
        if status is None:
            issues.append(issue(
                "warning", page_url, "broken_external_link",
                f"External link unreachable: {link}",
                "Remove or replace the broken external link."
            ))
        elif 400 <= status < 500:
            issues.append(issue(
                "warning", page_url, "http_4xx_external",
                f"External link returned {status}: {link}",
                "Remove or replace the broken external link."
            ))

    # 11. Broken image URLs
    for img in soup.find_all("img", src=True):
        src = urljoin(page_url, img["src"].strip())
        if not src.startswith("http"):
            continue
        status, _ = head_ok(src)
        if status is None or (400 <= status < 600):
            issues.append(issue(
                "warning", page_url, "broken_image",
                f"Image unreachable ({status}): {src}",
                "Replace or restore the missing image."
            ))


# ── Per-site checks ───────────────────────────────────────────────────────────
def crawl_site(base_url):
    issues = []
    visited = set()
    queue = [base_url]
    parsed_base = urlparse(base_url)

    # 1. Homepage reachable
    resp, elapsed = fetch(base_url)
    if resp is None or resp.status_code >= 400:
        status = resp.status_code if resp else "unreachable"
        issues.append(issue(
            "critical", base_url, "site_down",
            f"Homepage returned {status}.",
            "Investigate hosting / DNS immediately."
        ))
        return issues  # no point crawling further

    # 12. robots.txt
    robots_url = urljoin(base_url, "/robots.txt")
    r_robots, _ = fetch(robots_url)
    if r_robots is None or r_robots.status_code != 200:
        issues.append(issue(
            "warning", base_url, "missing_robots_txt",
            f"robots.txt not found at {robots_url}",
            "Create a robots.txt file at the site root."
        ))

    # 13. sitemap.xml
    sitemap_url = urljoin(base_url, "/sitemap.xml")
    r_sitemap, _ = fetch(sitemap_url)
    if r_sitemap is None or r_sitemap.status_code != 200:
        issues.append(issue(
            "warning", base_url, "missing_sitemap",
            f"sitemap.xml not found at {sitemap_url}",
            "Generate and submit a sitemap.xml."
        ))

    # BFS crawl
    pages_crawled = 0
    while queue and pages_crawled < MAX_PAGES_PER_SITE:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        resp, elapsed = fetch(url)
        if resp is None:
            issues.append(issue(
                "critical", url, "page_unreachable",
                "Page returned no response.",
                "Investigate server or DNS."
            ))
            continue

        if resp.status_code >= 400:
            lvl = "critical" if resp.status_code >= 500 else "critical"
            issues.append(issue(
                lvl, url, f"http_{resp.status_code}",
                f"Page returned HTTP {resp.status_code}.",
                "Fix or redirect the URL."
            ))
            continue

        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            continue

        pages_crawled += 1
        check_page(url, resp.text, elapsed, issues)

        # Discover more pages on same domain
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("mailto:", "tel:", "#", "javascript:")):
                continue
            full = urljoin(url, href)
            p = urlparse(full)
            if p.netloc == parsed_base.netloc and full not in visited:
                queue.append(full)

        time.sleep(CRAWL_DELAY)

    return issues


# ── Report building ───────────────────────────────────────────────────────────
def build_report(mode):
    now = datetime.now(timezone.utc)
    all_issues = []
    site_summaries = {}

    for site in SITES:
        print(f"  Crawling {site} …")
        site_issues = crawl_site(site)
        all_issues.extend(site_issues)
        site_summaries[site] = {
            "critical": sum(1 for i in site_issues if i["severity"] == "critical"),
            "warning": sum(1 for i in site_issues if i["severity"] == "warning"),
            "info": sum(1 for i in site_issues if i["severity"] == "info"),
        }

    total_critical = sum(s["critical"] for s in site_summaries.values())
    total_warning = sum(s["warning"] for s in site_summaries.values())

    report = {
        "generated_at": now.isoformat(),
        "mode": mode,
        "total_critical": total_critical,
        "total_warning": total_warning,
        "site_summaries": site_summaries,
        "issues": all_issues,
    }
    return report


# ── File output ───────────────────────────────────────────────────────────────
def save_reports(report):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    json_path = REPORT_DIR / f"report_{ts}.json"
    json_path.write_text(json.dumps(report, indent=2))

    txt_path = REPORT_DIR / f"report_{ts}.txt"
    txt_path.write_text(build_text_report(report))

    html_path = REPORT_DIR / f"report_{ts}.html"
    html_path.write_text(build_html_report(report))

    return json_path, txt_path, html_path


def build_text_report(report):
    lines = []
    lines.append("=" * 70)
    lines.append("MATTPOM DIGITAL VENTURES — WEBSITE HEALTH REPORT")
    lines.append(f"Generated: {report['generated_at']}  |  Mode: {report['mode'].upper()}")
    lines.append("=" * 70)
    lines.append(f"\nSUMMARY: {report['total_critical']} CRITICAL  |  {report['total_warning']} WARNINGS\n")

    for site, s in report["site_summaries"].items():
        lines.append(f"  {site}  →  {s['critical']} critical, {s['warning']} warnings, {s['info']} info")

    lines.append("\n" + "-" * 70)
    lines.append("CRITICAL ISSUES")
    lines.append("-" * 70)
    crits = [i for i in report["issues"] if i["severity"] == "critical"]
    if crits:
        for i in crits:
            lines.append(f"\n[{i['check'].upper()}]")
            lines.append(f"  Page  : {i['page_url']}")
            lines.append(f"  Detail: {i['detail']}")
            lines.append(f"  Fix   : {i['fix']}")
    else:
        lines.append("  None — great job!")

    lines.append("\n" + "-" * 70)
    lines.append("WARNINGS")
    lines.append("-" * 70)
    warns = [i for i in report["issues"] if i["severity"] == "warning"]
    if warns:
        for i in warns:
            lines.append(f"\n[{i['check'].upper()}]")
            lines.append(f"  Page  : {i['page_url']}")
            lines.append(f"  Detail: {i['detail']}")
            lines.append(f"  Fix   : {i['fix']}")
    else:
        lines.append("  None.")

    return "\n".join(lines)


def build_html_report(report):
    crits = [i for i in report["issues"] if i["severity"] == "critical"]
    warns = [i for i in report["issues"] if i["severity"] == "warning"]
    infos = [i for i in report["issues"] if i["severity"] == "info"]

    def rows(issues):
        if not issues:
            return "<tr><td colspan='4' style='color:#888'>None found.</td></tr>"
        out = []
        for i in issues:
            bg = "#fff0f0" if i["severity"] == "critical" else "#fffbe6" if i["severity"] == "warning" else "#f0f8ff"
            out.append(
                f"<tr style='background:{bg}'>"
                f"<td style='word-break:break-all'><a href='{i['page_url']}'>{i['page_url']}</a></td>"
                f"<td>{i['check']}</td>"
                f"<td>{i['detail']}</td>"
                f"<td>{i['fix']}</td>"
                f"</tr>"
            )
        return "\n".join(out)

    site_rows = ""
    for site, s in report["site_summaries"].items():
        site_rows += (
            f"<tr><td><a href='{site}'>{site}</a></td>"
            f"<td style='color:#c00'>{s['critical']}</td>"
            f"<td style='color:#b8860b'>{s['warning']}</td>"
            f"<td style='color:#555'>{s['info']}</td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Website Health Report</title>
<style>
body{{font-family:Arial,sans-serif;font-size:14px;color:#222;margin:20px}}
h1{{color:#1a1a2e}}h2{{color:#16213e;margin-top:30px}}
table{{border-collapse:collapse;width:100%;margin-bottom:20px}}
th{{background:#16213e;color:#fff;padding:8px 10px;text-align:left}}
td{{padding:7px 10px;border-bottom:1px solid #ddd;vertical-align:top}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-weight:bold}}
.crit{{background:#c00;color:#fff}}.warn{{background:#e6ac00;color:#fff}}.info{{background:#0078d4;color:#fff}}
</style></head>
<body>
<h1>🏥 Mattpom Digital Ventures — Website Health Report</h1>
<p><strong>Generated:</strong> {report['generated_at']} &nbsp;|&nbsp; <strong>Mode:</strong> {report['mode'].upper()}</p>
<p>
  <span class="badge crit">{report['total_critical']} Critical</span> &nbsp;
  <span class="badge warn">{report['total_warning']} Warnings</span> &nbsp;
  <span class="badge info">{len(infos)} Info</span>
</p>

<h2>Site Summary</h2>
<table>
<tr><th>Site</th><th>Critical</th><th>Warnings</th><th>Info</th></tr>
{site_rows}
</table>

<h2>🚨 Critical Issues</h2>
<table>
<tr><th>Page URL</th><th>Check</th><th>Detail</th><th>Recommended Fix</th></tr>
{rows(crits)}
</table>

<h2>⚠️ Warnings</h2>
<table>
<tr><th>Page URL</th><th>Check</th><th>Detail</th><th>Recommended Fix</th></tr>
{rows(warns)}
</table>

<h2>ℹ️ Info</h2>
<table>
<tr><th>Page URL</th><th>Check</th><th>Detail</th><th>Recommended Fix</th></tr>
{rows(infos)}
</table>
</body></html>"""


# ── Email ─────────────────────────────────────────────────────────────────────
def send_email(report, txt_body, html_body):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user)

    if not all([smtp_host, smtp_user, smtp_pass]):
        print("  ⚠  SMTP secrets not configured — skipping email.")
        return

    c = report["total_critical"]
    w = report["total_warning"]
    subject = (
        f"[{'🚨 CRITICAL' if c else '✅ OK'}] Website Health: "
        f"{c} critical, {w} warnings — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(txt_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, [EMAIL_TO], msg.as_string())
        print(f"  ✅ Email sent to {EMAIL_TO}")
    except Exception as exc:
        print(f"  ❌ Email failed: {exc}")


# ── Make.com webhook ──────────────────────────────────────────────────────────
def ping_webhook(report):
    url = os.environ.get("MAKE_WEBHOOK_URL")
    if not url:
        return
    try:
        payload = {
            "total_critical": report["total_critical"],
            "total_warning": report["total_warning"],
            "mode": report["mode"],
            "generated_at": report["generated_at"],
        }
        requests.post(url, json=payload, timeout=10)
        print("  ✅ Make.com webhook triggered.")
    except Exception as exc:
        print(f"  ⚠  Make.com webhook failed: {exc}")


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="daily", choices=["daily", "weekly", "monthly"])
    args = parser.parse_args()

    print(f"\n🔍 Website Health Monitor starting — mode={args.mode}")
    print(f"   {datetime.now(timezone.utc).isoformat()}\n")

    report = build_report(args.mode)
    json_path, txt_path, html_path = save_reports(report)

    print(f"\n📄 Reports saved:")
    print(f"   JSON : {json_path}")
    print(f"   TEXT : {txt_path}")
    print(f"   HTML : {html_path}")

    txt_body = txt_path.read_text()
    html_body = html_path.read_text()

    print("\n📧 Sending email …")
    send_email(report, txt_body, html_body)

    print("\n🔗 Pinging Make.com webhook …")
    ping_webhook(report)

    print(f"\n✅ Done — {report['total_critical']} critical, {report['total_warning']} warnings.\n")


if __name__ == "__main__":
    main()

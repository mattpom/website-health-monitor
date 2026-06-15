#!/usr/bin/env python3
"""
Website Health Monitor
Crawls configured sites and reports issues via email and Make.com webhook.
"""

import argparse
import json
import os
import re
import smtplib
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

# ── Constants ──────────────────────────────────────────────────────────────────

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"

ISSUE_FIXES = {
    "site_down": "Check hosting, DNS, and SSL certificate. Verify server is running.",
    "http_4xx": "Check the URL is correct and the page exists. Update or remove the link.",
    "http_5xx": "Server error — check hosting logs, plugins, or contact your host.",
    "broken_internal_link": "Update or remove the broken internal link on this page.",
    "broken_external_link": "Remove or replace the broken external link.",
    "broken_image": "Re-upload the image or update the image src URL.",
    "missing_amazon_tag": "Add 'brokemodelife-20' as the 'tag' parameter to this Amazon URL.",
    "redirect_chain": "Update the link to point directly to the final destination URL.",
    "slow_page": "Optimize images, enable caching, or check server response times.",
    "no_robots_txt": "Create a robots.txt file at the site root.",
    "no_sitemap": "Generate and submit a sitemap.xml — use an SEO plugin or Yoast.",
    "missing_title": "Add a unique, descriptive <title> tag to this page.",
    "missing_meta_description": "Add a meta description tag with 120–160 characters.",
    "missing_canonical": "Add a canonical URL tag to prevent duplicate content issues.",
    "etsy_link": "Verify this Etsy link is active and points to a live product/shop.",
    "pinterest_link": "Verify this Pinterest link is still active.",
    "twitter_x_link": "Verify this X/Twitter link is still active.",
    "instagram_link": "Verify this Instagram link is still active.",
    "booking_link": "Verify this Booking.com link is still active and not expired.",
    "getyourguide_link": "Verify this GetYourGuide link is still active.",
}


# ── Helpers ─────────────────────────────────────────────────────────────────────

def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def make_session(user_agent: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent})
    return s


def safe_get(session, url, timeout=15, allow_redirects=True):
    try:
        r = session.get(url, timeout=timeout, allow_redirects=allow_redirects)
        return r, None
    except requests.exceptions.SSLError as e:
        return None, f"SSL error: {e}"
    except requests.exceptions.ConnectionError as e:
        return None, f"Connection error: {e}"
    except requests.exceptions.Timeout:
        return None, "Timeout"
    except Exception as e:
        return None, str(e)


def is_same_domain(url: str, base: str) -> bool:
    return urlparse(url).netloc == urlparse(base).netloc


def normalize_url(url: str) -> str:
    p = urlparse(url)
    return p._replace(fragment="").geturl()


def detect_redirect_chain(session, url, timeout=15):
    """Follow redirects manually and return list of hops."""
    hops = [url]
    current = url
    for _ in range(10):
        r, err = safe_get(session, current, timeout=timeout, allow_redirects=False)
        if err or r is None:
            break
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location", "")
            if loc:
                next_url = urljoin(current, loc)
                hops.append(next_url)
                current = next_url
            else:
                break
        else:
            break
    return hops


def contains_affiliate_pattern(url: str, patterns: list) -> bool:
    return any(p in url for p in patterns)


def amazon_has_tag(url: str, expected_tag: str) -> bool:
    m = re.search(r"[?&]tag=([^&]+)", url)
    if m:
        return m.group(1) == expected_tag
    return False


# ── Crawler ──────────────────────────────────────────────────────────────────────

class SiteCrawler:
    def __init__(self, site_config: dict, global_config: dict):
        self.site = site_config
        self.cfg = global_config
        self.crawler_cfg = global_config.get("crawler", {})
        self.aff = global_config.get("affiliate_patterns", {})
        self.timeout = self.crawler_cfg.get("timeout", 15)
        self.max_pages = self.crawler_cfg.get("max_pages_per_site", 100)
        self.max_ext = self.crawler_cfg.get("max_external_links", 50)
        self.slow_threshold = self.crawler_cfg.get("slow_page_threshold_seconds", 3.0)
        self.amazon_tag = site_config.get("amazon_tag")
        self.base_url = site_config["url"].rstrip("/")
        self.session = make_session(self.crawler_cfg.get("user_agent", "WebHealthMonitor/1.0"))
        self.issues = []
        self.visited = set()
        self.queue = [self.base_url]
        self.ext_checked = set()

    def add_issue(self, severity, issue_type, page_url, detail, fix=None):
        self.issues.append({
            "severity": severity,
            "type": issue_type,
            "page_url": page_url,
            "detail": detail,
            "fix": fix or ISSUE_FIXES.get(issue_type, "Review and fix manually."),
        })

    def check_robots_sitemap(self):
        for path, issue_type in [("/robots.txt", "no_robots_txt"), ("/sitemap.xml", "no_sitemap")]:
            url = self.base_url + path
            r, err = safe_get(self.session, url, self.timeout)
            if err or r is None or r.status_code >= 400:
                self.add_issue(SEVERITY_WARNING, issue_type, url,
                               f"{path} not found or unreachable (status: {r.status_code if r else err})")

    def check_page(self, url):
        start = time.time()
        r, err = safe_get(self.session, url, self.timeout)
        elapsed = time.time() - start

        if err or r is None:
            self.add_issue(SEVERITY_CRITICAL, "site_down", url, f"Unreachable: {err}")
            return None

        status = r.status_code
        if 400 <= status < 500:
            self.add_issue(SEVERITY_CRITICAL, "http_4xx", url, f"HTTP {status}")
            return None
        if status >= 500:
            self.add_issue(SEVERITY_CRITICAL, "http_5xx", url, f"HTTP {status}")
            return None

        # Redirect chain check
        hops = detect_redirect_chain(self.session, url, self.timeout)
        if len(hops) > 2:
            self.add_issue(SEVERITY_WARNING, "redirect_chain", url,
                           f"Redirect chain ({len(hops)} hops): {' → '.join(hops)}")

        # Slow page
        if elapsed > self.slow_threshold:
            self.add_issue(SEVERITY_WARNING, "slow_page", url,
                           f"Page loaded in {elapsed:.1f}s (threshold: {self.slow_threshold}s)")

        soup = BeautifulSoup(r.text, "lxml")

        # SEO checks
        title = soup.find("title")
        if not title or not title.get_text(strip=True):
            self.add_issue(SEVERITY_WARNING, "missing_title", url, "No <title> tag found")

        meta_desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        if not meta_desc or not meta_desc.get("content", "").strip():
            self.add_issue(SEVERITY_WARNING, "missing_meta_description", url, "No meta description found")

        canonical = soup.find("link", attrs={"rel": re.compile("^canonical$", re.I)})
        if not canonical or not canonical.get("href", "").strip():
            self.add_issue(SEVERITY_WARNING, "missing_canonical", url, "No canonical URL tag found")

        return soup

    def check_links(self, page_url, soup):
        links = soup.find_all("a", href=True)
        for a in links:
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue

            full_url = normalize_url(urljoin(page_url, href))
            parsed = urlparse(full_url)
            if parsed.scheme not in ("http", "https"):
                continue

            internal = is_same_domain(full_url, self.base_url)

            # Affiliate / social pattern checks (on all links)
            self._check_affiliate_link(page_url, full_url)

            if internal:
                if full_url not in self.visited and full_url not in self.queue:
                    self.queue.append(full_url)
                # Check internal link status
                if full_url not in self.visited:
                    r, err = safe_get(self.session, full_url, self.timeout)
                    if err or r is None or r.status_code >= 400:
                        self.add_issue(SEVERITY_CRITICAL, "broken_internal_link", page_url,
                                       f"Broken internal link: {full_url} ({r.status_code if r else err})")
            else:
                if full_url not in self.ext_checked and len(self.ext_checked) < self.max_ext:
                    self.ext_checked.add(full_url)
                    r, err = safe_get(self.session, full_url, self.timeout)
                    if err or r is None or r.status_code >= 400:
                        self.add_issue(SEVERITY_WARNING, "broken_external_link", page_url,
                                       f"Broken external link: {full_url} ({r.status_code if r else err})")

    def _check_affiliate_link(self, page_url, url):
        # Amazon
        if contains_affiliate_pattern(url, self.aff.get("amazon", [])):
            if self.amazon_tag and not amazon_has_tag(url, self.amazon_tag):
                self.add_issue(SEVERITY_CRITICAL, "missing_amazon_tag", page_url,
                               f"Amazon link missing '{self.amazon_tag}' tag: {url}")
            else:
                self.add_issue(SEVERITY_INFO, "etsy_link" if "etsy" in url else "amazon_link",
                               page_url, f"Amazon link found: {url}")
        # Etsy
        if contains_affiliate_pattern(url, self.aff.get("etsy", [])):
            self.add_issue(SEVERITY_INFO, "etsy_link", page_url, f"Etsy link: {url}")
        # Pinterest
        if contains_affiliate_pattern(url, self.aff.get("pinterest", [])):
            self.add_issue(SEVERITY_INFO, "pinterest_link", page_url, f"Pinterest link: {url}")
        # Twitter/X
        if contains_affiliate_pattern(url, self.aff.get("twitter_x", [])):
            self.add_issue(SEVERITY_INFO, "twitter_x_link", page_url, f"X/Twitter link: {url}")
        # Instagram
        if contains_affiliate_pattern(url, self.aff.get("instagram", [])):
            self.add_issue(SEVERITY_INFO, "instagram_link", page_url, f"Instagram link: {url}")
        # Booking
        if contains_affiliate_pattern(url, self.aff.get("booking", [])):
            self.add_issue(SEVERITY_INFO, "booking_link", page_url, f"Booking.com link: {url}")
        # GetYourGuide
        if contains_affiliate_pattern(url, self.aff.get("getyourguide", [])):
            self.add_issue(SEVERITY_INFO, "getyourguide_link", page_url, f"GetYourGuide link: {url}")

    def check_images(self, page_url, soup):
        for img in soup.find_all("img", src=True):
            src = img["src"].strip()
            if not src or src.startswith("data:"):
                continue
            full_url = normalize_url(urljoin(page_url, src))
            r, err = safe_get(self.session, full_url, self.timeout)
            if err or r is None or r.status_code >= 400:
                self.add_issue(SEVERITY_WARNING, "broken_image", page_url,
                               f"Broken image: {full_url} ({r.status_code if r else err})")

    def run(self):
        print(f"\n{'='*60}")
        print(f"Crawling: {self.base_url}")
        print(f"{'='*60}")

        # robots + sitemap first
        self.check_robots_sitemap()

        pages_crawled = 0
        while self.queue and pages_crawled < self.max_pages:
            url = self.queue.pop(0)
            if url in self.visited:
                continue
            self.visited.add(url)
            pages_crawled += 1

            print(f"  [{pages_crawled}/{self.max_pages}] {url}")
            soup = self.check_page(url)
            if soup:
                self.check_links(url, soup)
                self.check_images(url, soup)

        criticals = sum(1 for i in self.issues if i["severity"] == SEVERITY_CRITICAL)
        warnings = sum(1 for i in self.issues if i["severity"] == SEVERITY_WARNING)
        print(f"  Done. Pages: {pages_crawled} | Issues: {criticals} critical, {warnings} warnings")
        return self.issues


# ── Report Generation ────────────────────────────────────────────────────────────

def generate_reports(all_results: dict, mode: str, run_ts: str):
    os.makedirs("reports", exist_ok=True)
    slug = run_ts.replace(":", "-").replace(" ", "_")

    total_critical = sum(
        sum(1 for i in issues if i["severity"] == SEVERITY_CRITICAL)
        for issues in all_results.values()
    )
    total_warning = sum(
        sum(1 for i in issues if i["severity"] == SEVERITY_WARNING)
        for issues in all_results.values()
    )

    # JSON
    json_path = f"reports/health_{mode}_{slug}.json"
    with open(json_path, "w") as f:
        json.dump({
            "run_at": run_ts,
            "mode": mode,
            "summary": {"critical": total_critical, "warnings": total_warning},
            "sites": all_results,
        }, f, indent=2)

    # Plain text
    txt_path = f"reports/health_{mode}_{slug}.txt"
    with open(txt_path, "w") as f:
        f.write(build_text_report(all_results, mode, run_ts, total_critical, total_warning))

    # HTML
    html_path = f"reports/health_{mode}_{slug}.html"
    with open(html_path, "w") as f:
        f.write(build_html_report(all_results, mode, run_ts, total_critical, total_warning))

    return json_path, txt_path, html_path, total_critical, total_warning


def build_text_report(all_results, mode, run_ts, total_critical, total_warnings):
    lines = []
    lines.append(f"WEBSITE HEALTH REPORT — {mode.upper()}")
    lines.append(f"Run: {run_ts}")
    lines.append(f"Summary: {total_critical} CRITICAL | {total_warnings} WARNINGS")
    lines.append("=" * 70)

    for site_name, issues in all_results.items():
        crits = [i for i in issues if i["severity"] == SEVERITY_CRITICAL]
        warns = [i for i in issues if i["severity"] == SEVERITY_WARNING]
        lines.append(f"\n▶ {site_name}  ({len(crits)} critical, {len(warns)} warnings)")
        lines.append("-" * 50)
        for sev, group in [("CRITICAL", crits), ("WARNING", warns)]:
            for issue in group:
                lines.append(f"  [{sev}] {issue['type'].upper().replace('_', ' ')}")
                lines.append(f"    Page : {issue['page_url']}")
                lines.append(f"    Issue: {issue['detail']}")
                lines.append(f"    Fix  : {issue['fix']}")
                lines.append("")

    lines.append("=" * 70)
    lines.append("Mattpom Digital Ventures — Website Health Monitor")
    return "\n".join(lines)


def build_html_report(all_results, mode, run_ts, total_critical, total_warnings):
    sev_color = {SEVERITY_CRITICAL: "#dc3545", SEVERITY_WARNING: "#fd7e14", SEVERITY_INFO: "#6c757d"}

    rows_by_site = ""
    for site_name, issues in all_results.items():
        crits = [i for i in issues if i["severity"] == SEVERITY_CRITICAL]
        warns = [i for i in issues if i["severity"] == SEVERITY_WARNING]
        rows_by_site += f"""
        <h3 style="margin-top:24px;color:#333">▶ {site_name}
          <span style="font-size:13px;font-weight:normal;color:#888">
            {len(crits)} critical &bull; {len(warns)} warnings
          </span>
        </h3>
        <table width="100%" cellpadding="8" cellspacing="0" style="border-collapse:collapse;font-size:13px">
          <thead>
            <tr style="background:#f5f5f5">
              <th align="left" width="90">Severity</th>
              <th align="left" width="160">Issue</th>
              <th align="left">Page URL</th>
              <th align="left">Detail</th>
              <th align="left">Fix</th>
            </tr>
          </thead>
          <tbody>
        """
        for issue in sorted(issues, key=lambda x: (x["severity"] != SEVERITY_CRITICAL, x["severity"])):
            if issue["severity"] == SEVERITY_INFO:
                continue
            color = sev_color.get(issue["severity"], "#333")
            rows_by_site += f"""
            <tr style="border-bottom:1px solid #eee">
              <td><span style="color:{color};font-weight:bold">{issue['severity']}</span></td>
              <td>{issue['type'].replace('_', ' ').title()}</td>
              <td style="word-break:break-all;max-width:200px"><a href="{issue['page_url']}" style="color:#0066cc">{issue['page_url']}</a></td>
              <td style="word-break:break-all">{issue['detail']}</td>
              <td>{issue['fix']}</td>
            </tr>
            """
        rows_by_site += "</tbody></table>"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Website Health Report</title></head>
<body style="font-family:Arial,sans-serif;max-width:1100px;margin:0 auto;padding:20px;color:#333">
  <div style="background:#1a1a2e;color:white;padding:20px 24px;border-radius:8px;margin-bottom:24px">
    <h1 style="margin:0;font-size:20px">🔍 Website Health Report — {mode.upper()}</h1>
    <p style="margin:6px 0 0;opacity:.8;font-size:13px">Run: {run_ts}</p>
  </div>
  <div style="display:flex;gap:16px;margin-bottom:24px">
    <div style="flex:1;background:#fff5f5;border:1px solid #ffcccc;border-radius:8px;padding:16px;text-align:center">
      <div style="font-size:36px;font-weight:bold;color:#dc3545">{total_critical}</div>
      <div style="color:#888;font-size:13px">CRITICAL</div>
    </div>
    <div style="flex:1;background:#fff8f0;border:1px solid #ffd8a8;border-radius:8px;padding:16px;text-align:center">
      <div style="font-size:36px;font-weight:bold;color:#fd7e14">{total_warnings}</div>
      <div style="color:#888;font-size:13px">WARNINGS</div>
    </div>
  </div>
  {rows_by_site}
  <p style="margin-top:32px;font-size:11px;color:#aaa;text-align:center">
    Mattpom Digital Ventures — Website Health Monitor
  </p>
</body>
</html>"""


# ── Email ─────────────────────────────────────────────────────────────────────────

def send_email(cfg: dict, txt_report: str, html_report: str, total_critical: int, total_warnings: int, mode: str):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user)

    if not all([smtp_host, smtp_user, smtp_pass]):
        print("⚠️  SMTP secrets not set — skipping email.")
        return

    to_addr = cfg["email"]["to"]
    prefix = cfg["email"].get("subject_prefix", "[Website Health]")
    subject = f"{prefix} {mode.upper()} — {total_critical} Critical, {total_warnings} Warnings"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_addr
    msg.attach(MIMEText(txt_report, "plain"))
    msg.attach(MIMEText(html_report, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, [to_addr], msg.as_string())
        print(f"✅ Email sent to {to_addr}")
    except Exception as e:
        print(f"❌ Email failed: {e}")


# ── Make.com Webhook ──────────────────────────────────────────────────────────────

def send_webhook(all_results: dict, total_critical: int, total_warnings: int, mode: str, run_ts: str):
    webhook_url = os.environ.get("MAKE_WEBHOOK_URL")
    if not webhook_url:
        return
    payload = {
        "run_at": run_ts,
        "mode": mode,
        "total_critical": total_critical,
        "total_warnings": total_warnings,
        "sites": {
            name: {
                "critical": sum(1 for i in issues if i["severity"] == SEVERITY_CRITICAL),
                "warnings": sum(1 for i in issues if i["severity"] == SEVERITY_WARNING),
            }
            for name, issues in all_results.items()
        },
    }
    try:
        r = requests.post(webhook_url, json=payload, timeout=15)
        print(f"✅ Webhook sent: {r.status_code}")
    except Exception as e:
        print(f"⚠️  Webhook failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Website Health Monitor")
    parser.add_argument("--mode", default="daily", choices=["daily", "weekly", "monthly"])
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    all_results = {}
    for site in cfg["sites"]:
        crawler = SiteCrawler(site, cfg)
        issues = crawler.run()
        all_results[site["name"]] = issues

    json_path, txt_path, html_path, total_critical, total_warnings = generate_reports(
        all_results, args.mode, run_ts
    )

    print(f"\n📄 Reports saved: {json_path}, {txt_path}, {html_path}")
    print(f"📊 Total: {total_critical} critical, {total_warnings} warnings\n")

    with open(txt_path) as f:
        txt_report = f.read()
    with open(html_path) as f:
        html_report = f.read()

    send_email(cfg, txt_report, html_report, total_critical, total_warnings, args.mode)
    send_webhook(all_results, total_critical, total_warnings, args.mode, run_ts)

    if total_critical > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

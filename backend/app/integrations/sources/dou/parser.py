"""Parses DOU's RSS feed (discovery) and job detail HTML (full description).

RSS is the discovery mechanism — far more stable than scraping listing HTML — and
detail HTML is only fetched for jobs not already seen. See docs/source-adapters.md.
Tested against fixtures in tests/fixtures/dou/.
"""

import re
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

from bs4 import BeautifulSoup

_VACANCY_ID_RE = re.compile(r"/vacancies/(\d+)/")


def parse_rss_feed(rss_xml: str) -> list[dict[str, Any]]:
    """Return a list of {external_id, url, title, description_html, published_at}
    discovered in the feed, in feed order."""
    root = ElementTree.fromstring(rss_xml)
    entries = []
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        match = _VACANCY_ID_RE.search(link)
        if not match:
            continue
        pub_date_raw = item.findtext("pubDate")
        entries.append(
            {
                "external_id": match.group(1),
                "url": link,
                "title": (item.findtext("title") or "").strip(),
                "description_html": item.findtext("description") or "",
                "published_at": parsedate_to_datetime(pub_date_raw) if pub_date_raw else None,
            }
        )
    return entries


def parse_job_detail(html: str) -> dict[str, Any]:
    """Extract structured fields from a DOU vacancy detail page."""
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("h1.g-h2")
    company_el = soup.select_one(".info .l-n a")
    location_el = soup.select_one(".sh-info .place")
    salary_el = soup.select_one(".sh-info .salary")
    description_el = soup.select_one(".b-typo.vacancy-section")
    remote_link = soup.select_one('a[href*="&remote"], a[href*="?remote"]')
    location_text = location_el.get_text(strip=True) if location_el else None

    return {
        "title": title_el.get_text(strip=True) if title_el else "",
        "company": company_el.get_text(strip=True) if company_el else "",
        "location_text": location_text,
        "salary_text": salary_el.get_text(strip=True) if salary_el else None,
        "description_html": str(description_el) if description_el else "",
        "remote": remote_link is not None
        or (location_text is not None and "віддалено" in location_text.lower()),
    }

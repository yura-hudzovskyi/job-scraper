"""Parses DOU's RSS feed (discovery) and job detail HTML (full description).

RSS is the discovery mechanism — far more stable than scraping listing HTML — and
detail HTML is only fetched for jobs not already seen. See docs/source-adapters.md.
Tested against fixtures in tests/fixtures/dou/.
"""


def parse_rss_feed(rss_xml: str) -> list[dict]:
    """Return a list of {external_id, url, title, published_at} discovered in the feed."""
    raise NotImplementedError


def parse_job_detail(html: str) -> dict:
    """Extract the full job payload (description, company, etc.) from a detail page."""
    raise NotImplementedError

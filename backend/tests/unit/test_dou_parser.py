"""Parser regression tests against saved fixtures — see docs/source-adapters.md.

Drop real fixture files into tests/fixtures/dou/ before un-skipping these.
"""

from pathlib import Path

import pytest

from app.integrations.sources.dou import parser

FIXTURES = Path(__file__).parent.parent / "fixtures" / "dou"


@pytest.mark.skip(reason="pending real DOU RSS/HTML fixtures")
def test_parse_rss_feed_extracts_jobs() -> None:
    rss_xml = (FIXTURES / "listing.html").read_text()
    jobs = parser.parse_rss_feed(rss_xml)
    assert jobs


@pytest.mark.skip(reason="pending real DOU RSS/HTML fixtures")
def test_parse_job_detail_extracts_description() -> None:
    html = (FIXTURES / "vacancy.html").read_text()
    job = parser.parse_job_detail(html)
    assert job["description"]

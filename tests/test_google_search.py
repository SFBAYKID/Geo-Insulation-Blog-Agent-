import json

import httpx
import pytest

from geo_blog.competitors import pick_results
from geo_blog.google_search import GoogleSearchUnavailable, google_results
from geo_blog.settings import Settings


def settings():
    return Settings(_env_file=None, serpapi_api_key="private-test-key")


def test_google_organic_order_excludes_ads_and_forums():
    def handler(request):
        assert request.url.params["engine"] == "google"
        assert request.url.params["q"] == "cold email automation"
        assert request.url.params["no_cache"] == "true"
        return httpx.Response(
            200,
            json={
                "search_metadata": {"status": "Success", "id": "example"},
                "search_parameters": {
                    "engine": "google",
                    "q": "cold email automation",
                    "api_key": "private-test-key",
                },
                "ads": [{"position": 1, "link": "https://ads.test/"}],
                "organic_results": [
                    {"position": i, "link": u, "title": "Cold email automation guide"}
                    for i, u in enumerate(
                        [
                            "https://one.test/",
                            "https://reddit.com/r/example",
                            "https://two.test/",
                            "https://three.test/",
                        ],
                        1,
                    )
                ],
            },
        )

    result = google_results(
        settings(),
        "cold email automation",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert "private-test-key" not in json.dumps(result)
    assert [p["result_position"] for p in pick_results(result, "cold email automation")] == [
        1,
        3,
        4,
    ]
    assert result["source"] == "google_serpapi"


def test_wrong_engine_or_service_error_rejected():
    for payload in [
        {"error": "API quota exceeded"},
        {
            "search_metadata": {"status": "Success"},
            "search_parameters": {"engine": "bing", "q": "x"},
        },
    ]:
        with pytest.raises(GoogleSearchUnavailable):
            google_results(
                settings(),
                "x",
                httpx.Client(
                    transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
                ),
            )


def test_http_error_does_not_expose_credential():
    with pytest.raises(GoogleSearchUnavailable) as e:
        google_results(
            settings(),
            "x",
            httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(401))),
        )
    assert "private-test-key" not in str(e.value)


def test_missing_key_never_makes_request():
    with pytest.raises(GoogleSearchUnavailable):
        google_results(Settings(_env_file=None), "x")


def test_browser_preference_does_not_call_search_service(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from geo_blog.competitors import collect, utcnow

    raw = {
        "source": "google_browser",
        "query": "x",
        "checked_at": utcnow(),
        "search_url": "https://www.google.com/search?q=x",
        "results": [],
    }
    (tmp_path / "google-search.json").write_text(json.dumps(raw))
    call = Mock(side_effect=AssertionError("Must not request paid search"))
    monkeypatch.setattr("geo_blog.google_search.google_results", call)
    report = collect(settings(), {"keyword": "x"}, tmp_path)
    call.assert_not_called()
    assert report["source"] == "google_browser"


def test_thin_serp_is_retryable_not_evidence():
    """September 20, 2026: a 200 OK scrape returned eight unrelated pages and two
    eligible competitors, so the night aborted instead of asking Google again."""
    degraded = {
        "search_metadata": {"status": "Success", "id": "example"},
        "search_parameters": {
            "engine": "google",
            "q": "refund processed email template",
        },
        "organic_results": [
            {"position": i, "link": u, "title": u}
            for i, u in enumerate(
                [
                    "https://www.cbp.gov/document/guidance/refund-enrollment",
                    "https://www.reddit.com/r/ParisTravelGuide/comments/1mysw6x/tax_refund/",
                    "https://www.youtube.com/watch?v=lZZEOTgrvtE",
                    "https://www.youtube.com/watch?v=KQ0CboZaM0w",
                    "https://www.irs.gov/refunds",
                ],
                1,
            )
        ],
    }
    with pytest.raises(GoogleSearchUnavailable):
        google_results(
            settings(),
            "refund processed email template",
            httpx.Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=degraded))
            ),
        )


def test_off_topic_serp_is_retryable_not_evidence():
    """September 22, 2026: a 200 OK scrape for "conference follow up email template"
    returned a Platformer story, an Instagram reel and schema.org as the top pages."""
    degraded = {
        "search_metadata": {"status": "Success", "id": "example"},
        "search_parameters": {
            "engine": "google",
            "q": "conference follow up email template",
        },
        "organic_results": [
            {"position": i, "link": u, "title": t}
            for i, (u, t) in enumerate(
                [
                    (
                        "https://www.platformer.news/the-withering-email/",
                        "The withering email that got an ethical AI researcher fired",
                    ),
                    (
                        "https://www.instagram.com/reel/DdCmOIIhtlx/",
                        "email is the bane of me professional life",
                    ),
                    ("https://schema.org/email", "email - Property"),
                    ("https://zachholman.com/posts/cold-email", "The Cold Email"),
                ],
                1,
            )
        ],
    }
    with pytest.raises(GoogleSearchUnavailable):
        google_results(
            settings(),
            "conference follow up email template",
            httpx.Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=degraded))
            ),
        )


def test_on_topic_titles_accept_hyphenated_variants():
    from geo_blog.google_search import on_topic

    assert on_topic(
        "How to Write the Perfect Follow-Up Email After a Conference",
        "conference follow up email template",
    )
    assert not on_topic("email - Property", "conference follow up email template")


def test_on_topic_accepts_plurals_and_synonyms():
    """September 23, 2026: real Google results for this keyword were rejected as off-topic."""
    from geo_blog.google_search import on_topic

    keyword = "meeting reminder text message sample"
    assert on_topic("10 Meeting Reminder Message Templates to Reduce No- ...", keyword)
    assert on_topic("The 12 Best Appointment Reminder Text/SMS Templates", keyword)
    assert on_topic("80+ Ways: How to Send a Reminder Text (Tips & Templates)", keyword)
    assert not on_topic("The withering email that got an ethical AI researcher fired", keyword)
    assert not on_topic(
        "email is the bane of me professional life",
        "conference follow up email template",
    )

import json
from unittest.mock import Mock

from geo_blog.competitors import collect, extract_counts, pick_results, update_thread
from geo_blog.settings import Settings
from geo_blog.store import Store


def results():
    from geo_blog.competitors import utcnow

    return {
        "source": "google_browser",
        "query": "cold email automation",
        "checked_at": utcnow(),
        "search_url": "https://www.google.com/search?q=cold+email+automation",
        "results": [
            {"kind": "organic_article", "position": i, "url": u, "title": u}
            for i, u in enumerate(
                [
                    "https://geo-insulation.com/blog/",
                    "https://one.test/post",
                    "https://www.one.test/duplicate",
                    "https://two.test/post",
                    "https://three.test/post",
                    "https://four.test/post",
                ],
                1,
            )
        ],
    }


def test_selection_uses_exact_query_real_results_and_distinct_domains():
    pages = pick_results(results(), "cold email automation")
    assert [p["url"] for p in pages] == [
        "https://one.test/post",
        "https://two.test/post",
        "https://three.test/post",
    ]
    assert pages[0]["result_position"] == 2
    import pytest

    with pytest.raises(ValueError):
        pick_results(results(), "other keyword")


def test_counts_exclude_chrome_but_keep_article_heading():
    html = (
        "<body><header>cold email automation</header><nav>cold email automation</nav><main><article><header><h1>Cold Email Automation</h1></header><p>"
        + "ordinary text " * 60
        + "</p><p>cold\nemail automation; ai cold email; cold email automations</p><script>cold email automation</script><p hidden>cold email automation</p></article></main><footer>cold email automation</footer></body>"
    )
    counts, text = extract_counts(html, "cold email automation", "ai cold email")
    assert counts["primary_mentions"] == 2
    assert counts["secondary_mentions"] == 1
    assert counts["words"] == len(text.split())
    assert counts["scope"] == "article"


def test_audit_failure_does_not_become_zero_or_drop_competitor(tmp_path):
    report = collect(
        Settings(_env_file=None),
        {"keyword": "cold email automation"},
        tmp_path,
        search=lambda _: results(),
        measure=Mock(side_effect=TimeoutError),
        audit=Mock(side_effect=TimeoutError),
    )
    assert len(report["pages"]) == 3
    assert report["complete"]
    assert all("lighthouse" not in p and "content" not in p for p in report["pages"])
    assert json.loads((tmp_path / "report.json").read_text()) == report


def test_update_edits_root_without_touching_approval_and_is_idempotent(tmp_path):
    s = Settings(_env_file=None, storage_dir=tmp_path)
    store = Store(tmp_path)
    topic = {
        "keyword": "cold email automation",
        "secondary_keyword": "ai cold email",
        "topic": "Test",
    }
    store.reserve("one", "day", "topic")
    store.save("one", {"topic": topic, "markdown": "immutable"})
    store.set_thread("one", s.slack_channel_id, "100.1")
    store.claim_delivery("one", s.slack_channel_id)
    store.delivered("one", "101.1")
    before = store.get("one")
    report = {
        "keyword": topic["keyword"],
        "secondary_keyword": topic["secondary_keyword"],
        "searched_at": "today",
        "method": "Method",
        "pages": [],
        "complete": True,
        "source": "google_browser",
    }
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    client.files_upload_v2.return_value = {"files": [{"permalink": "https://slack.com/file"}]}
    collector = Mock(return_value=report)
    update_thread(s, store, "one", client=client, collector=collector)
    update_thread(s, store, "one", client=client, collector=collector)
    assert store.get("one") == before
    assert client.chat_update.call_count == 1
    assert client.chat_update.call_args.kwargs["ts"] == "100.1"
    client.files_upload_v2.assert_not_called()
    client.chat_postMessage.assert_not_called()


def test_search_failure_is_explicit(tmp_path):
    r = collect(
        Settings(_env_file=None),
        {"keyword": "x"},
        tmp_path,
        search=Mock(side_effect=TimeoutError),
    )
    assert r["pages"] == [] and r["search_error"] and r["complete"]


def test_marketing_card_is_not_mistaken_for_whole_article():
    html = (
        "<body><main>Short hero</main><article>Small card</article><section>"
        + "word " * 100
        + "cold email automation</section></body>"
    )
    counts, _ = extract_counts(html, "cold email automation", "")
    assert counts["scope"] == "body" and counts["primary_mentions"] == 1


def test_private_urls_rejected(monkeypatch):
    import pytest

    from geo_blog.competitors import public_url

    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a: [(None, None, None, None, ("127.0.0.1", 80))]
    )
    with pytest.raises(ValueError):
        public_url("http://localhost/")


def test_non_google_results_and_ads_cannot_be_ranked():
    import pytest

    with pytest.raises(ValueError):
        pick_results(
            {"content": [{"type": "web_search_result", "url": "https://dripify.com/"}]},
            "cold email automation",
        )
    raw = results()
    raw["results"][1]["kind"] = "ad"
    raw["results"][2]["kind"] = "organic_forum"
    assert all("one.test" not in p["url"] for p in pick_results(raw, "cold email automation"))


def test_unverified_future_run_does_not_substitute_search_provider(tmp_path):
    r = collect(Settings(_env_file=None), {"keyword": "cold email automation"}, tmp_path)
    assert r["pages"] == [] and r["search_error"]


def test_snapshot_displays_actual_position_and_source():
    from geo_blog.competitors import slack_text, utcnow

    report = {
        "keyword": "ai email reply",
        "secondary_keyword": "ai email response generator",
        "source": "google_serpapi",
        "searched_at": utcnow(),
        "location": "San Francisco",
        "search_url": "https://www.google.com/search?q=ai+email+reply",
        "pages": [{"title": "Example", "url": "https://example.com/", "result_position": 6}],
    }
    text = slack_text(report)
    assert "Google organic position in this search: #6" in text
    assert "snapshot" in text and "SerpApi" in text
    assert "Open a current Google search" in text


def test_explicit_refresh_replaces_cached_report_not_draft(tmp_path):
    from geo_blog.competitors import utcnow

    s = Settings(_env_file=None, storage_dir=tmp_path)
    st = Store(tmp_path)
    st.reserve("one", "day", "topic")
    st.save("one", {"topic": {"keyword": "test"}, "markdown": "original"})
    st.set_thread("one", s.slack_channel_id, "100.1")
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    old = {
        "source": "google_serpapi",
        "searched_at": utcnow(),
        "keyword": "test",
        "pages": [],
        "complete": True,
    }
    update_thread(s, st, "one", client=client, collector=Mock(return_value=old))
    before = st.get("one")
    fresh = dict(
        old,
        pages=[
            {"url": f"https://site{i}.test", "title": str(i), "result_position": i}
            for i in [1, 2, 3]
        ],
    )
    collector = Mock(return_value=fresh)
    update_thread(s, st, "one", client=client, collector=collector, refresh=True)
    collector.assert_called_once()
    assert st.get("one") == before
    assert client.chat_update.call_count == 2
    assert list((tmp_path / "one/competitors/history").glob("*.json"))
    import pytest

    with pytest.raises(ValueError):
        update_thread(
            s,
            st,
            "one",
            client=client,
            collector=Mock(return_value=dict(old, search_error="unavailable")),
            refresh=True,
        )
    assert client.chat_update.call_count == 2
    with st.db() as db:
        assert (
            json.loads(db.execute("SELECT report FROM competitor_reports").fetchone()[0]) == fresh
        )
    client.chat_update.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        update_thread(
            s,
            st,
            "one",
            client=client,
            collector=Mock(return_value=fresh),
            refresh=True,
        )
    with st.db() as db:
        assert db.execute("SELECT updated FROM competitor_reports").fetchone()[0] == 0
    client.chat_update.side_effect = None
    update_thread(
        s,
        st,
        "one",
        client=client,
        collector=Mock(side_effect=AssertionError("Must reuse verified report")),
    )
    with st.db() as db:
        assert db.execute("SELECT updated FROM competitor_reports").fetchone()[0] == 1


def test_corrected_secondary_recounts_saved_evidence_without_changing_rankings_or_scores(
    tmp_path,
):
    import hashlib

    s = Settings(_env_file=None, storage_dir=tmp_path)
    st = Store(tmp_path)
    topic = {
        "keyword": "handoff email template",
        "secondary_keyword": "MEASURED SUPPORTING KEYWORDS",
    }
    st.reserve("one", "day", "topic")
    st.save("one", {"topic": topic})
    st.set_thread("one", s.slack_channel_id, "100.1")
    folder = tmp_path / "one/competitors"
    folder.mkdir(parents=True)
    text = "handoff email template hand off email template"
    (folder / "page-1.txt").write_text(text)
    old = {
        "source": "google_serpapi",
        "searched_at": "2026-09-17T04:00:00Z",
        **topic,
        "pages": [
            {
                "title": "Page",
                "url": "https://example.com",
                "result_position": 3,
                "content": {
                    "words": 8,
                    "primary_mentions": 1,
                    "secondary_mentions": 0,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                },
                "lighthouse": {"scores": {"performance": 45}},
            }
        ],
        "complete": True,
    }
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    update_thread(s, st, "one", topic, client, Mock(return_value=old))
    before = st.get("one")
    collector = Mock(side_effect=AssertionError("Reuse saved measurement"))
    corrected = dict(topic, secondary_keyword="hand off email template")
    report = update_thread(s, st, "one", corrected, client, collector)
    assert report["pages"][0]["content"]["secondary_mentions"] == 1
    assert report["pages"][0]["lighthouse"] == old["pages"][0]["lighthouse"]
    assert (
        report["searched_at"] == old["searched_at"] and report["pages"][0]["result_position"] == 3
    )
    assert st.get("one") == before
    assert "MEASURED SUPPORTING KEYWORDS" not in client.chat_update.call_args.kwargs["text"]
    assert json.loads((folder / "report.json").read_text()) == report
    assert list((folder / "history").glob("*.json"))
    update_thread(s, st, "one", corrected, client, collector)
    assert client.chat_update.call_count == 2


def test_changed_competitor_text_preserves_slack_and_database(tmp_path):
    import pytest

    s = Settings(_env_file=None, storage_dir=tmp_path)
    st = Store(tmp_path)
    topic = {"keyword": "test", "secondary_keyword": "old"}
    st.reserve("one", "day", "topic")
    st.save("one", {"topic": topic})
    st.set_thread("one", s.slack_channel_id, "100.1")
    folder = tmp_path / "one/competitors"
    folder.mkdir(parents=True)
    (folder / "page-1.txt").write_text("changed")
    old = {
        "source": "google_serpapi",
        "searched_at": "today",
        **topic,
        "pages": [
            {
                "title": "Page",
                "url": "https://example.com",
                "content": {
                    "words": 1,
                    "primary_mentions": 0,
                    "secondary_mentions": 0,
                    "text_sha256": "mismatch",
                },
            }
        ],
    }
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    update_thread(s, st, "one", topic, client, Mock(return_value=old))
    with pytest.raises(ValueError, match="text changed"):
        update_thread(s, st, "one", dict(topic, secondary_keyword="new"), client)
    assert client.chat_update.call_count == 1
    with st.db() as db:
        assert json.loads(db.execute("SELECT report FROM competitor_reports").fetchone()[0]) == old

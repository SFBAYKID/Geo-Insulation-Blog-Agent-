"""Basecamp queue and delivery safety using only mock network transports."""

import json
from contextlib import nullcontext

import httpx
import pytest

from geo_blog.basecamp_api import Basecamp
from geo_blog.basecamp_queue import (
    brief_attachments,
    has_existing_draft,
    select_basecamp_topic,
    topic_from_task,
    validate_basecamp_draft,
)
from geo_blog.basecamp_review import draft_ready
from geo_blog.settings import Settings
from geo_blog.store import Store


def settings(tmp_path):
    return Settings(
        _env_file=None,
        storage_dir=tmp_path,
        blog_queue_source="basecamp",
        basecamp_client_id="test",
        basecamp_client_secret="test",
        basecamp_refresh_token="test",
        basecamp_account_id="1",
        basecamp_project_id=2,
        basecamp_todolist_id=3,
        basecamp_reviewer_id=4,
        basecamp_draft_ready_enabled=True,
        website_preview_enabled=True,
    )


def task(ident=5, completed=False):
    return {
        "id": ident,
        "content": 'Blog A4 — Write /blog/test-blog for "test keyword"',
        "description": "<div>Working title and H1: Test blog</div><div>Supporting terms to cover naturally: none</div>",
        "status": "active",
        "completed": completed,
        "updated_at": "version1",
        "bucket": {"id": 2},
        "parent": {"id": 3},
        "app_url": f"https://app.basecamp.com/1/buckets/2/todos/{ident}",
    }


class FakeApi:
    def __init__(self, s):
        self.settings = s
        self.bucket = "buckets/2/"
        self.rows = [task()]
        self.comment_rows = []
        self.sent = []
        self.fail = False

    def listing(self, path):
        return self.rows

    def task(self, ident):
        return next(t for t in self.rows if t["id"] == ident)

    def comments(self, ident):
        return self.comment_rows

    def reviewer(self):
        return {"id": 4, "name": "the reviewer", "attachable_sgid": 'signed<&"'}

    def request(self, method, path, **kwargs):
        assert method == "POST" and path == "buckets/2/recordings/5/comments.json"
        self.sent.append(kwargs["json"])
        if self.fail:
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(201, json={"id": 9, "app_url": "https://app.basecamp.com/comment9"})


def saved(s):
    store = Store(s.storage_dir)
    topic = topic_from_task(task(), [], [])
    draft = {
        "topic": topic,
        "front_matter": {"slug": "test-blog", "title": "Title <safe>"},
        "report": {"passed": True, "word_count": 2100},
        "preview_url": "https://example.vercel.app/blog/test-blog",
        "preview_commit": "abc",
        "state": "ready",
        "production_lighthouse": {"scores": {"seo": 100}},
    }
    assert store.reserve("draft", "test-run", "basecamp:5")
    store.save("draft", draft)
    return store, draft


def test_queue_respects_tasks_and_reservations(tmp_path, monkeypatch):
    s = settings(tmp_path)
    api = FakeApi(s)
    api.rows = [task(6, True), task()]
    monkeypatch.setattr("geo_blog.basecamp_queue.Basecamp", lambda _: nullcontext(api))
    topic = select_basecamp_topic(s, set())
    assert topic["id"] == "basecamp:5"
    assert topic["required_slug"] == "test-blog" and topic["minimum_words"] == 2000
    assert topic["source_record_ids"] == [] and topic["secondary_keyword"] == ""
    assert select_basecamp_topic(s, {"basecamp:5"}) is None
    assert select_basecamp_topic(s, set(), "recJulyRow") is None
    api.comment_rows = [{"id": 10, "content": "Draft ready for review."}]
    assert select_basecamp_topic(s, set()) is None


def test_skip_review_not_brief():
    assert not has_existing_draft([{"content": "SEO brief attached. Draft to this brief."}])
    assert has_existing_draft([{"content": "Blog A4 draft ready for review."}])
    assert has_existing_draft([{"content": "Published and verified live."}])


def test_draft_shape_enforced(tmp_path):
    _, d = saved(settings(tmp_path))
    validate_basecamp_draft(d)
    d["front_matter"]["slug"] = "different"
    with pytest.raises(ValueError, match="slug"):
        validate_basecamp_draft(d)
    d["front_matter"]["slug"] = "test-blog"
    d["report"]["word_count"] = 1999
    with pytest.raises(ValueError, match="minimum"):
        validate_basecamp_draft(d)


def test_review_mentions_basecamp_reviewer_once_and_keeps_task_open(tmp_path, monkeypatch):
    s = settings(tmp_path)
    store, d = saved(s)
    api = FakeApi(s)
    monkeypatch.setattr("geo_blog.basecamp_review.Basecamp", lambda _: nullcontext(api))
    first = draft_ready(s, store, "draft")
    assert draft_ready(s, store, "draft") == first
    assert len(api.sent) == 1
    content = api.sent[0]["content"]
    assert 'sgid="signed&lt;&amp;&quot;"' in content
    assert "Title &lt;safe&gt;" in content and d["preview_url"] in content
    assert "not published" in content and "remain open" in content
    assert api.rows[0]["completed"] is False
    assert store.required("draft")["status"] == "ready"


def test_timeout_reconciles_but_never_blindly_reposts(tmp_path, monkeypatch):
    s = settings(tmp_path)
    store, _ = saved(s)
    api = FakeApi(s)
    api.fail = True
    monkeypatch.setattr("geo_blog.basecamp_review.Basecamp", lambda _: nullcontext(api))
    with pytest.raises(httpx.ReadTimeout):
        draft_ready(s, store, "draft")
    with pytest.raises(ValueError, match="unresolved"):
        draft_ready(s, store, "draft")
    assert len(api.sent) == 1
    api.comment_rows = [
        {"id": 9, "app_url": "https://app.basecamp.com/comment9", "content": api.sent[0]["content"]}
    ]
    assert draft_ready(s, store, "draft") == "https://app.basecamp.com/comment9"
    assert len(api.sent) == 1


@pytest.mark.parametrize("change", ["completed", "wrong_page", "existing_draft", "no_preview"])
def test_review_holds_conflicting_work(tmp_path, monkeypatch, change):
    s = settings(tmp_path)
    store, draft = saved(s)
    api = FakeApi(s)
    if change == "completed":
        api.rows[0]["completed"] = True
    elif change == "wrong_page":
        api.rows[0]["content"] = api.rows[0]["content"].replace("test-blog", "other-blog")
    elif change == "existing_draft":
        api.comment_rows = [{"id": 2, "content": "draft ready"}]
    else:
        draft.pop("preview_url")
        with store.db() as db:
            db.execute("UPDATE drafts SET payload=?", (json.dumps(draft),))
    monkeypatch.setattr("geo_blog.basecamp_review.Basecamp", lambda _: nullcontext(api))
    with pytest.raises(ValueError):
        draft_ready(s, store, "draft")
    assert api.sent == []


def test_api_pagination_does_not_leak_token(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if "authorization/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "private"})
        return httpx.Response(
            200, json=[], headers={"Link": '<https://evil.test/capture>; rel="next"'}
        )

    with Basecamp(settings(tmp_path), httpx.Client(transport=httpx.MockTransport(handler))) as api:
        with pytest.raises(ValueError, match="outside"):
            api.listing("people.json")
    assert len(calls) == 2
    assert str(calls[-1].url) == "https://3.basecampapi.com/1/people.json"


def test_brief_download_strips_bearer_on_redirect(tmp_path):
    def handler(request):
        if request.url.host == "3.basecampapi.com":
            assert request.headers["Authorization"] == "Bearer test"
            return httpx.Response(302, headers={"Location": "https://cdn.example.test/brief"})
        assert "Authorization" not in request.headers
        return httpx.Response(200, text="# Brief", headers={"Content-Type": "text/markdown"})

    api = Basecamp(settings(tmp_path), httpx.Client(transport=httpx.MockTransport(handler)))
    api.headers["Authorization"] = "Bearer test"
    records = [
        {
            "content_attachments": [
                {
                    "filename": "A4.md",
                    "download_url": "https://3.basecampapi.com/1/blobs/test/download/A4.md",
                    "byte_size": 7,
                }
            ]
        }
    ]
    assert brief_attachments(api, records)[0]["text"] == "# Brief"


def test_brief_rejects_login_html(tmp_path):
    api = Basecamp(
        settings(tmp_path),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, text="<html>Sign in</html>", headers={"Content-Type": "text/html"}
                )
            )
        ),
    )
    records = [
        {
            "content_attachments": [
                {
                    "filename": "A4.md",
                    "download_url": "https://3.basecampapi.com/1/blobs/test/download/A4.md",
                    "byte_size": 20,
                }
            ]
        }
    ]
    with pytest.raises(ValueError, match="sign-in"):
        brief_attachments(api, records)


def test_basecamp_selection_never_falls_back_to_airtable(tmp_path, monkeypatch):
    from geo_blog.topics import select_topic

    def fail(*args):
        raise ValueError("Basecamp unavailable")

    monkeypatch.setattr("geo_blog.basecamp_queue.select_basecamp_topic", fail)
    with pytest.raises(ValueError, match="Basecamp unavailable"):
        select_topic(settings(tmp_path), set())


def test_working_title_without_exact_keyword_gets_a_minimal_adjustment_note():
    # Fixture title "Test blog" lacks "test keyword": the website check would reject that H1.
    assert 'exact primary keyword "test keyword"' in topic_from_task(task(), [], [])["notes"]
    matching = dict(task(), description="<div>Working title and H1: A test keyword guide</div>")
    assert "exact primary keyword" not in topic_from_task(matching, [], [])["notes"]

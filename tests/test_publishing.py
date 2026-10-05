import json
from unittest.mock import Mock

import httpx
import pytest

from geo_blog.publishing import (
    copy_article,
    enqueue_existing,
    run_next,
    verify_live,
)
from geo_blog.settings import Settings
from geo_blog.store import Store


def setup(tmp_path):
    settings = Settings(
        _env_file=None, storage_dir=tmp_path, slack_approver_ids="reviewer"
    ).model_copy(update={"publishing_enabled": True})
    store = Store(tmp_path)
    store.reserve("draft", "run", "topic")
    store.save(
        "draft",
        {
            "markdown": "reviewed",
            "preview_url": "https://preview.vercel.app/blog/hub/slug/",
        },
    )
    store.set_thread("draft", settings.slack_channel_id, "100.1")
    store.claim_delivery("draft", settings.slack_channel_id)
    store.delivered("draft", "101.1")
    return settings, store


def approve(settings, store, **overrides):
    args = dict(
        decision="approved",
        user="reviewer",
        channel=settings.slack_channel_id,
        message_ts="101.1",
        allowed_users=settings.approvers,
        publish=True,
    )
    args.update(overrides)
    return store.decide("draft", **args)


def jobs(store):
    with store.db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM publish_jobs")]


def test_approval_atomically_snapshots_and_deduplicates(tmp_path):
    settings, store = setup(tmp_path)
    assert approve(settings, store)
    assert store.get("draft")["status"] == "publishing"
    assert not approve(settings, store)
    assert len(jobs(store)) == 1
    assert jobs(store)[0]["payload"] == store.get("draft")["payload"]


@pytest.mark.parametrize(
    "override", [{"user": "other"}, {"message_ts": "old.1"}, {"channel": "other"}]
)
def test_unauthorized_and_stale_cards_cannot_publish(tmp_path, override):
    settings, store = setup(tmp_path)
    assert not approve(settings, store, **override)
    assert jobs(store) == []


def test_rejection_never_queues(tmp_path):
    settings, store = setup(tmp_path)
    assert approve(settings, store, decision="rejected")
    assert jobs(store) == []


def test_explicit_old_approval_only(tmp_path):
    settings, store = setup(tmp_path)
    with pytest.raises(ValueError):
        enqueue_existing(settings, store, "draft")
    approve(settings, store, publish=False)
    assert jobs(store) == []
    enqueue_existing(settings, store, "draft")
    assert len(jobs(store)) == 1


def test_worker_success_is_threaded_and_not_repeated(tmp_path):
    settings, store = setup(tmp_path)
    approve(settings, store)
    client = Mock()
    client.auth_test.return_value = {"team_id": settings.slack_team_id}
    client.chat_postMessage.return_value = {"ts": "102.1"}
    publisher = Mock(return_value="https://geo-insulation.com/blog/hub/slug/")
    run_next(settings, store, client, publisher)
    assert store.get("draft")["status"] == "published"
    assert json.loads(store.get("draft")["payload"])["live_url"].startswith(
        "https://geo-insulation.com/"
    )
    assert client.chat_postMessage.call_args.kwargs["thread_ts"] == "100.1"
    run_next(settings, store, client, publisher)
    assert publisher.call_count == 1
    assert client.chat_postMessage.call_count == 1


def test_changed_payload_cancels_job(tmp_path):
    settings, store = setup(tmp_path)
    approve(settings, store)
    with store.db() as db:
        db.execute("UPDATE drafts SET payload='{}'")
    publisher = Mock()
    run_next(settings, store, Mock(), publisher)
    publisher.assert_not_called()
    assert jobs(store)[0]["state"] == "cancelled"


def test_failed_publish_preserves_approval_and_never_claims_live(tmp_path):
    settings, store = setup(tmp_path)
    approve(settings, store)
    client = Mock()
    client.auth_test.return_value = {"team_id": settings.slack_team_id}
    with pytest.raises(RuntimeError):
        run_next(settings, store, client, Mock(side_effect=RuntimeError("deploy failed")))
    assert store.get("draft")["status"] == "publish_failed"
    assert store.get("draft")["reviewer"] == "reviewer"
    assert "couldn’t complete" in client.chat_postMessage.call_args.kwargs["text"]
    assert jobs(store)[0]["state"] == "failed"


def test_uncertain_slack_notification_does_not_republish(tmp_path):
    settings, store = setup(tmp_path)
    approve(settings, store)
    client = Mock()
    client.auth_test.return_value = {"team_id": settings.slack_team_id}
    client.chat_postMessage.side_effect = TimeoutError()
    publisher = Mock(return_value="https://geo-insulation.com/blog/hub/slug/")
    with pytest.raises(TimeoutError):
        run_next(settings, store, client, publisher)
    assert store.get("draft")["status"] == "published"
    assert jobs(store)[0]["state"] == "notifying"
    run_next(settings, store, client, publisher)
    assert publisher.call_count == 1
    assert client.chat_postMessage.call_count == 1


def test_only_visibility_and_date_change_and_no_other_drafts_copied(tmp_path):
    source = tmp_path / "source"
    checkout = tmp_path / "target"
    source.mkdir()
    checkout.mkdir()
    (source / "public/blog/media").mkdir(parents=True)
    (source / "public/blog/media/hero.webp").write_bytes(b"art")
    (source / "unapproved.mdx").write_text("other draft")
    raw = b"---\ntitle: Approved\ndraft: true\ndate: 2026-09-15\nhero:\n  src: /blog/media/hero.webp\n---\n\nExact reviewed body.\n"
    paths, meta = copy_article(source, checkout, "src/content/blog/hub/slug.mdx", raw, "2026-09-16")
    assert meta["draft"] is False
    assert set(paths) == {
        "src/content/blog/hub/slug.mdx",
        "public/blog/media/hero.webp",
    }
    assert (checkout / paths[0]).read_text().endswith("\n\nExact reviewed body.\n")
    assert not (checkout / "unapproved.mdx").exists()
    (checkout / paths[0]).write_text("existing different article")
    with pytest.raises(ValueError):
        copy_article(source, checkout, paths[0], raw, "2026-09-16")


def test_flat_publication_copies_only_deterministic_reviewed_route(tmp_path):
    from geo_blog.website import flat_routes

    source = tmp_path / "source"
    checkout = tmp_path / "target"
    route = "src/app/blog/handoff-email-template/page.tsx"
    for name, content in flat_routes("installation-process", "handoff-email-template").items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    origin = source / route
    raw = b"---\ntitle: Approved\ndraft: true\ndate: 2026-09-16\npath: /blog/handoff-email-template/\n---\n\nExact approved article.\n"
    paths, meta = copy_article(
        source,
        checkout,
        "src/content/blog/installation-process/handoff-email-template.mdx",
        raw,
        "2026-09-17",
    )
    assert set(paths) == {
        "src/content/blog/installation-process/handoff-email-template.mdx",
        route,
        "src/app/blog/handoff-email-template/opengraph-image.tsx",
    }
    assert (checkout / route).read_bytes() == origin.read_bytes()
    assert meta["path"] == "/blog/handoff-email-template/"
    origin.write_text("arbitrary source code")
    with pytest.raises(ValueError, match="Unreviewed flat route"):
        copy_article(
            source,
            tmp_path / "other",
            "src/content/blog/installation-process/handoff-email-template.mdx",
            raw,
            "2026-09-17",
        )
    assert not (tmp_path / "other").exists()


def test_flat_approval_source_uses_saved_hub_content_and_rejects_route_tampering(
    tmp_path, monkeypatch
):
    import hashlib

    from geo_blog import publishing
    from geo_blog.website import export_preview

    valid_article = "---\ntitle: Handoff email template\nslug: handoff-email-template\ndescription: A reviewed handoff guide\nkeywords: [handoff email template]\n---\n# Handoff guide\n\nReviewed article.\n"
    s = Settings(_env_file=None, storage_dir=tmp_path)
    st = Store(tmp_path)
    folder = tmp_path / "flat"
    site = folder / "website"
    payload = {
        "markdown": valid_article,
        "topic": {
            "hub": "installation-process",
            "planned_url": "/blog/handoff-email-template/",
        },
        "preview_url": "https://preview.vercel.app/blog/handoff-email-template/",
    }
    st.reserve("flat", "day", "topic")
    st.save("flat", payload)
    path = export_preview(st, "flat", site)
    relative = "src/content/blog/installation-process/handoff-email-template.mdx"
    expected = publishing.digest(valid_article)
    (folder / "deployment.json").write_text(
        json.dumps(
            {
                "path": path,
                "preview_url": payload["preview_url"],
                "article_hash": expected,
                "sha": "checked",
            }
        )
    )
    (folder / "export.json").write_text(
        json.dumps(
            {
                "path": path,
                "mdx_path": relative,
                "article_hash": expected,
                "mdx_hash": hashlib.sha256((site / relative).read_bytes()).hexdigest(),
            }
        )
    )
    (folder / "hosted-qa.json").write_text(
        json.dumps(
            {
                "sha": "checked",
                "url": payload["preview_url"],
                "scores": {
                    "performance": 95,
                    "accessibility": 95,
                    "best-practices": 100,
                },
            }
        )
    )
    monkeypatch.setattr(
        publishing,
        "command",
        lambda args, *rest: "checked" if args[1] == "rev-parse" else "",
    )
    job = {"payload": json.dumps(payload), "draft_id": "flat"}
    assert publishing.approved_source(s, job)[1] == relative
    (site / "src/app/blog/handoff-email-template/page.tsx").write_text("changed")
    with pytest.raises(ValueError, match="route was modified"):
        publishing.approved_source(s, job)


def test_live_verification_rejects_noindex_or_missing_listing():
    url = "https://geo-insulation.com/blog/hub/slug/"
    article = f'<h1>Approved</h1><link rel="canonical" href="{url}">'
    pages = {
        url: article,
        "https://geo-insulation.com/blog/": '<a href="/blog/hub/slug/">Approved</a>',
        "https://geo-insulation.com/sitemap.xml": url,
        "https://geo-insulation.com/robots.txt": "User-agent: *\nAllow: /",
    }
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=pages[str(r.url)]))
    )
    verify_live(url, "Approved", None, client)
    pages["https://geo-insulation.com/robots.txt"] = "User-agent: *\nDisallow: /"
    with pytest.raises(ValueError, match="Robots rules"):
        verify_live(url, "Approved", None, client)
    pages["https://geo-insulation.com/robots.txt"] = "User-agent: *\nAllow: /"
    pages[url] += '<meta name="robots" content="noindex">'
    with pytest.raises(ValueError, match="indexable"):
        verify_live(url, "Approved", None, client)
    pages[url] = article
    pages["https://geo-insulation.com/blog/"] = ""
    with pytest.raises(ValueError, match="listing"):
        verify_live(url, "Approved", None, client)


def test_required_checks_cannot_be_bypassed_by_admin(monkeypatch, tmp_path):
    from geo_blog import publishing

    settings = Settings(_env_file=None)

    def command(args, *rest):
        endpoint = args[2]
        if endpoint.endswith("/protection"):
            return json.dumps({"required_status_checks": {"contexts": ["Content", "Lighthouse"]}})
        if "/check-runs?" in endpoint:
            return json.dumps(
                {
                    "check_runs": [
                        {
                            "id": 1,
                            "name": "Content",
                            "status": "completed",
                            "conclusion": "success",
                        },
                        {
                            "id": 2,
                            "name": "Lighthouse",
                            "status": "completed",
                            "conclusion": "failure",
                        },
                    ]
                }
            )
        return json.dumps({"statuses": []})

    monkeypatch.setattr(publishing, "command", command)
    with pytest.raises(RuntimeError, match="Required website checks failed"):
        publishing.wait_required_checks(settings, "sha", tmp_path, tmp_path / "log")


def test_retry_reuses_same_approved_job_and_rejects_changed_content(tmp_path):
    from geo_blog.publishing import retry_failed

    settings, store = setup(tmp_path)
    approve(settings, store)
    key = jobs(store)[0]["id"]
    with store.db() as db:
        db.execute("UPDATE publish_jobs SET state='failed'")
        db.execute("UPDATE drafts SET status='publish_failed'")
    assert retry_failed(settings, store, "draft") == key
    assert len(jobs(store)) == 1
    with store.db() as db:
        db.execute("UPDATE publish_jobs SET state='failed'")
        db.execute("UPDATE drafts SET status='publish_failed',payload='{}'")
    with pytest.raises(ValueError):
        retry_failed(settings, store, "draft")


@pytest.mark.parametrize("state", ["blocked", "dirty", "unstable", "unknown"])
def test_admin_never_bypasses_other_branch_rules(state):
    from geo_blog.publishing import assert_merge_ready

    settings = Settings(_env_file=None)
    pr = {
        "head": {"sha": "approved"},
        "base": {"ref": "main"},
        "mergeable": True,
        "mergeable_state": state,
    }
    with pytest.raises(RuntimeError, match="branch rules"):
        assert_merge_ready(pr, settings, "approved")
    pr["mergeable_state"] = "clean"
    assert_merge_ready(pr, settings, "approved")


def airtable(fields, writes=None):
    """A stand-in Airtable record that records every write it is asked to make."""
    state = dict(fields)

    def handler(request):
        if request.method == "PATCH":
            body = json.loads(request.content)
            if writes is not None:
                writes.append(body)
            state.update(body["fields"])
        return httpx.Response(200, json={"id": "recAbC123", "fields": state})

    return httpx.Client(transport=httpx.MockTransport(handler))


def publish_job(payload=None):
    return {
        "payload": json.dumps(payload if payload is not None else {"topic": {"id": "recAbC123"}})
    }


def airtable_settings(tmp_path, **kwargs):
    kwargs.setdefault("airtable_write_enabled", True)
    return Settings(
        _env_file=None,
        storage_dir=tmp_path,
        airtable_base_id="appBase",
        airtable_table="Blog Posts",
        airtable_token="key",
        **kwargs,
    )


def test_publication_marks_the_keyword_row_published_and_changes_nothing_else(tmp_path):
    from geo_blog.publishing import mark_airtable_published

    writes = []
    fields = {
        "Status": "Proposed",
        "Primary keyword": "ticket closure email template",
        "Notes / rationale": "keep me",
    }
    result = mark_airtable_published(
        airtable_settings(tmp_path), publish_job(), tmp_path, airtable(fields, writes)
    )
    assert result["updated"] and result["previous_status"] == "Proposed"
    assert writes == [{"fields": {"Status": "Published"}}]
    saved = json.loads((tmp_path / "airtable-status.json").read_text())
    assert saved["before"]["Primary keyword"] == "ticket closure email template"


@pytest.mark.parametrize("status", ["Retarget needed", "Fold into existing", "On hold"])
def test_publication_never_overwrites_an_editorial_status(tmp_path, status):
    from geo_blog.publishing import mark_airtable_published

    writes = []
    result = mark_airtable_published(
        airtable_settings(tmp_path),
        publish_job(),
        tmp_path,
        airtable({"Status": status}, writes),
    )
    assert not result["updated"] and writes == []
    assert result["reason"] == "Left the editorial status unchanged"


def test_publication_leaves_an_already_published_row_alone(tmp_path):
    from geo_blog.publishing import mark_airtable_published

    writes = []
    result = mark_airtable_published(
        airtable_settings(tmp_path),
        publish_job(),
        tmp_path,
        airtable({"Status": "Published"}, writes),
    )
    assert (
        not result["updated"]
        and writes == []
        and result["reason"] == "The row already reads Published"
    )


def test_no_airtable_write_happens_until_it_is_explicitly_enabled(tmp_path):
    from geo_blog.publishing import mark_airtable_published

    writes = []
    settings = airtable_settings(tmp_path, airtable_write_enabled=False)
    assert not mark_airtable_published(
        settings, publish_job(), tmp_path, airtable({"Status": "Proposed"}, writes)
    )["updated"]
    assert writes == []
    assert not mark_airtable_published(
        airtable_settings(tmp_path),
        publish_job({"topic": {}}),
        tmp_path,
        airtable({"Status": "Proposed"}, writes),
    )["updated"]
    assert writes == []


def test_a_rejected_row_update_never_undoes_a_verified_publication(tmp_path, monkeypatch):
    from geo_blog import publishing

    settings, store = setup(tmp_path)
    approve(settings, store)
    monkeypatch.setattr(settings, "airtable_write_enabled", True)
    monkeypatch.setattr(
        publishing,
        "mark_airtable_published",
        Mock(side_effect=httpx.HTTPError("403 forbidden")),
    )
    client = Mock()
    client.auth_test.return_value = {"team_id": settings.slack_team_id}
    client.chat_postMessage.return_value = {"ts": "102.1"}
    live = publishing.run_next(
        settings,
        store,
        client,
        Mock(return_value="https://geo-insulation.com/blog/hub/slug/"),
    )
    assert live == "https://geo-insulation.com/blog/hub/slug/"
    assert store.get("draft")["status"] == "published"
    text = client.chat_postMessage.call_args.kwargs["text"]
    assert "is live" in text and "still needs a manual change" in text


def test_public_publish_is_disabled_before_any_side_effect(tmp_path):
    from geo_blog.publishing import publish

    with pytest.raises(RuntimeError, match="deployment is disabled"):
        publish(Settings(_env_file=None), Store(tmp_path), {}, tmp_path)

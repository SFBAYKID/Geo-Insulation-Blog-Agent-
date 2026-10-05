import copy
import json
from unittest.mock import Mock

import pytest

from geo_blog.conversation import initialize as chat_initialize
from geo_blog.revisions import enqueue, run_next
from geo_blog.settings import Settings
from geo_blog.store import Store


def setup(tmp_path):
    s = Settings(
        _env_file=None,
        storage_dir=tmp_path,
        slack_approver_ids="owner",
        revisions_enabled=True,
    )
    st = Store(tmp_path)
    chat_initialize(st)
    st.reserve("nightly-2026-09-15", "daily:day", "keyword-id")
    base = {
        "markdown": "Original",
        "front_matter": {
            "title": "Original title",
            "description": "Original description",
        },
        "report": {"word_count": 1200},
        "topic": {"keyword": "AI workflows", "secondary_keyword": "review"},
        "preview_url": "https://old.vercel.app/blog/test/",
        "nightly_run": True,
        "google_verified": True,
    }
    st.save("nightly-2026-09-15", base)
    st.set_thread("nightly-2026-09-15", s.slack_channel_id, "100.1")
    st.claim_delivery("nightly-2026-09-15", s.slack_channel_id)
    st.delivered("nightly-2026-09-15", "101.1")
    c = Mock()
    c.auth_test.return_value = {"team_id": s.slack_team_id}
    c.chat_postMessage.return_value = {"ts": "102.1"}
    return s, st, c, base


def queue(s, st, event="event"):
    return enqueue(s, st, st.get("nightly-2026-09-15"), "Shorten the opening", event, "owner")


def prepare(s, job, folder, evidence):
    draft = json.loads(job["base_payload"])
    draft["markdown"] = "Revised"
    draft["revision_summary"] = "Shortened the opening."
    draft.pop("preview_url")
    return draft


def deploy(s, st, draft_id):
    st.attach_preview(draft_id, "https://new.vercel.app/blog/test/", "passed")
    return {"preview_url": "https://new.vercel.app/blog/test/"}


def test_queue_idempotency_and_stale_approval(tmp_path):
    s, st, c, base = setup(tmp_path)
    used = st.used_topics()
    assert queue(s, st)["queued"]
    assert queue(s, st)["queued"]
    assert not queue(s, st, "other")["saved"]
    assert not st.decide(
        "nightly-2026-09-15",
        decision="approved",
        user="owner",
        channel=s.slack_channel_id,
        message_ts="101.1",
        allowed_users={"owner"},
    )
    assert json.loads(st.get("nightly-2026-09-15")["payload"]) == base
    assert st.used_topics() == used
    with st.db() as db:
        assert db.execute("SELECT COUNT(*) FROM revision_jobs").fetchone()[0] == 1


def test_success_replaces_payload_and_requires_new_card_approval(tmp_path):
    s, st, c, base = setup(tmp_path)
    queue(s, st)
    run_next(s, st, c, prepare_fn=prepare, deploy_fn=deploy)
    row = st.get("nightly-2026-09-15")
    assert row["status"] == "pending" and row["message_ts"] == "102.1"
    assert json.loads(row["payload"])["markdown"] == "Revised"
    posted = c.chat_postMessage.call_args.kwargs
    assert posted["thread_ts"] == "100.1" and not posted["reply_broadcast"]
    assert posted["blocks"][-1]["type"] == "actions"
    assert all(b["type"] != "actions" for b in c.chat_update.call_args.kwargs["blocks"])
    assert not st.decide(
        row["id"],
        decision="approved",
        user="owner",
        channel=s.slack_channel_id,
        message_ts="101.1",
        allowed_users={"owner"},
    )
    assert st.decide(
        row["id"],
        decision="approved",
        user="owner",
        channel=s.slack_channel_id,
        message_ts="102.1",
        allowed_users={"owner"},
    )
    assert run_next(s, st, c, prepare_fn=prepare, deploy_fn=deploy) is None
    assert c.chat_postMessage.call_count == 1


def test_failed_checks_preserve_original_and_send_no_approval(tmp_path):
    s, st, c, base = setup(tmp_path)
    queue(s, st)
    with pytest.raises(RuntimeError):
        run_next(
            s,
            st,
            c,
            prepare_fn=prepare,
            deploy_fn=Mock(side_effect=RuntimeError("audit failed")),
        )
    row = st.get("nightly-2026-09-15")
    assert row["status"] == "revision_failed"
    assert json.loads(row["payload"]) == base
    assert "blocks" not in c.chat_postMessage.call_args.kwargs
    assert queue(s, st, "retry")["queued"]


def test_uncertain_delivery_not_repeated(tmp_path):
    s, st, c, base = setup(tmp_path)
    queue(s, st)
    c.chat_postMessage.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        run_next(s, st, c, prepare_fn=prepare, deploy_fn=deploy)
    assert run_next(s, st, c, prepare_fn=prepare, deploy_fn=deploy) is None
    assert c.chat_postMessage.call_count == 1
    assert json.loads(st.get("nightly-2026-09-15")["payload"]) == base
    with st.db() as db:
        assert db.execute("SELECT state FROM revision_jobs").fetchone()[0] == "delivering"


def test_restart_reuses_prepared_content(tmp_path):
    s, st, c, base = setup(tmp_path)
    queue(s, st)

    with st.db() as db:
        job = dict(db.execute("SELECT * FROM revision_jobs").fetchone())
        db.execute("UPDATE revision_jobs SET state='working'")
    saved = Store(tmp_path / "revisions" / job["id"])
    saved.reserve(job["id"], job["id"], "isolated")
    saved.save(job["id"], prepare(s, job, None, None))
    writer = Mock(side_effect=AssertionError("Must not regenerate"))
    run_next(s, st, c, prepare_fn=writer, deploy_fn=deploy)
    writer.assert_not_called()


def test_revision_waits_for_nightly_build_lock(tmp_path):
    import fcntl

    s, st, c, base = setup(tmp_path)
    queue(s, st)
    with (tmp_path / "nightly.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run_next(s, st, c, prepare_fn=prepare, deploy_fn=deploy) is None
    c.chat_update.assert_not_called()
    with st.db() as db:
        assert db.execute("SELECT state FROM revision_jobs").fetchone()[0] == "queued"


def test_plan_rejects_changed_slug_and_unknown_links():
    import yaml

    from geo_blog.revisions import validate_plan

    fm = {
        "title": "AI workflow automation: start here",
        "slug": "start-here",
        "description": "AI workflow automation starts with one clear handoff. Learn how to choose a task, check the result, and keep a person in control of each decision.",
        "keywords": ["AI workflow automation"],
    }
    body = (
        (
            "# AI workflow automation\n\nAI workflow automation starts with one clear handoff. "
            + "A clear task has inputs, actions, exceptions and a review step. " * 75
        )
        + "\n## Choose\n[Company](https://geo-insulation.com/)\n## Check\n[Docs](https://example.com/docs)\n## Review\n[Research](https://example.org/research)"
    )
    text = "---\n" + yaml.safe_dump(fm) + "---\n" + body
    base = {
        "markdown": text,
        "front_matter": fm,
        "sources": [
            "https://geo-insulation.com/",
            "https://example.com/docs",
            "https://example.org/research",
        ],
        "topic": {
            "keyword": "AI workflow automation",
            "product": {"url": "https://geo-insulation.com/"},
        },
    }
    plan = {
        "markdown": text.replace("## Choose", "## Choose a task"),
        "summary": "Clarified a heading",
        "hero": None,
        "exercise": None,
    }
    assert validate_plan(base, plan)["markdown"] == plan["markdown"]
    with pytest.raises(ValueError, match="slug"):
        validate_plan(
            base,
            dict(
                plan,
                markdown=plan["markdown"].replace("slug: start-here", "slug: other"),
            ),
        )
    with pytest.raises(ValueError):
        validate_plan(
            base,
            dict(
                plan,
                markdown=plan["markdown"] + "\n[Unverified](https://invented.example/)",
            ),
        )
    with pytest.raises(ValueError, match="No requested change"):
        validate_plan(base, dict(plan, markdown=text))


def test_shorter_opening_claim_is_measured(monkeypatch):
    from types import SimpleNamespace

    import geo_blog.revisions as revisions

    fm = {"slug": "test"}
    base = {
        "markdown": "---\nslug: test\n---\n# Title\n\nOne two three four.\n\n## Rest\nPreserve “quotes”.",
        "front_matter": fm,
        "sources": [],
        "topic": {"keyword": "AI", "product": {"url": "https://geo-insulation.com/"}},
    }
    monkeypatch.setattr(
        revisions,
        "validate",
        lambda *a: (
            fm,
            "[Product](https://geo-insulation.com/)",
            SimpleNamespace(passed=True, summary={}),
        ),
    )
    monkeypatch.setattr(revisions, "parse_draft", lambda text: ({}, text.split("---\n", 2)[2]))
    request = "Shorten the opening. Keep the remaining article unchanged."
    plan = {
        "markdown": base["markdown"].replace("One two three four.", "One two three four five."),
        "summary": "Shortened",
        "hero": None,
        "exercise": None,
    }
    with pytest.raises(ValueError, match="actually be shorter"):
        revisions.validate_plan(base, plan, request)
    plan["markdown"] = base["markdown"].replace("One two three four.", "One two.")
    assert revisions.validate_plan(base, plan, request)["markdown"] == plan["markdown"]
    plan["markdown"] = plan["markdown"].replace("“quotes”", '"quotes"')
    with pytest.raises(ValueError, match="verbatim"):
        revisions.validate_plan(base, plan, request)


def test_change_summary_uses_actual_lengths():
    import yaml

    from geo_blog.revisions import change_summary

    fm = {
        "title": "Title",
        "slug": "title",
        "description": "Description",
        "keywords": ["AI"],
    }
    prefix = "---\n" + yaml.safe_dump(fm) + "---\n# Title\n\n"
    base = {
        "markdown": prefix + "One two three four.\n\n## Next\nSame text.",
        "front_matter": fm,
        "topic": {"hero": {"src": "old"}},
    }
    revised = copy.deepcopy(base)
    revised["markdown"] = prefix + "One two.\n\n## Next\nSame text."
    revised["topic"]["hero"] = {"src": "new"}
    assert (
        change_summary(base, revised)
        == "Shortened the opening from 4 to 2 words. Created a new hero image."
    )

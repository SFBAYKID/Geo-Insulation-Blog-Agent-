"""Publication cannot merge unapproved, changed or failed website commits."""

import json
from unittest.mock import Mock

import pytest

from geo_blog import production_publish as pub
from geo_blog.settings import Settings


def setup_run(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path,
        production_delivery_enabled=True,
        slack_production_channel_id="C_GEO_PRODUCTION_TEST",
        slack_approver_ids="U_GEO_APPROVER_ONE,U_GEO_APPROVER_TWO",
    )
    plan = {
        "armed": True,
        "draft_id": "draft",
        "head_sha": "head",
        "base_sha": "base",
        "pr_number": 43,
    }
    review = {
        "state": "queued",
        "draft_id": "draft",
        "preview_commit": "head",
        "reviewer": "U_GEO_APPROVER_ONE",
        "message_ts": "card",
        "blocks": [],
    }
    review["approved_comments_digest"] = pub.comments_digest(review)
    (tmp_path / "publication-plan.json").write_text(json.dumps(plan))
    (tmp_path / "production-review.json").write_text(json.dumps(review))
    root = tmp_path / "draft"
    (root / "website").mkdir(parents=True)
    (root / "payload.json").write_text(json.dumps({"preview_commit": "head"}))
    pr = {
        "head": {"sha": "head", "repo": {"full_name": pub.REPOSITORY}},
        "base": {"ref": "main", "repo": {"full_name": pub.REPOSITORY}},
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "state": "open",
    }
    calls = []

    def api(checkout, endpoint, *args):
        calls.append((endpoint, args))
        if endpoint.endswith("/merge"):
            return {"merged": True, "sha": "merged"}
        if endpoint.endswith("/main"):
            return {"object": {"sha": "base"}}
        return pr

    monkeypatch.setattr(pub, "api", api)
    monkeypatch.setattr(
        pub, "command", lambda args, cwd: "head" if args[:2] == ["git", "rev-parse"] else ""
    )
    monkeypatch.setattr(pub, "require_ci_quality", Mock())
    monkeypatch.setattr(pub, "wait_production", Mock())
    monkeypatch.setattr(
        pub, "verify_live", Mock(return_value="https://geo-insulation.com/blog/example")
    )
    monkeypatch.setattr(pub, "update_card", Mock())
    return settings, pr, calls


def test_approved_commit_merges_once_then_verifies(tmp_path, monkeypatch):
    s, _, calls = setup_run(tmp_path, monkeypatch)
    assert pub.run_once(s)
    assert json.loads((tmp_path / "production-review.json").read_text())["state"] == "published"
    merges = [args for url, args in calls if url.endswith("/merge")]
    assert len(merges) == 1 and "sha=head" in merges[0]
    assert not pub.run_once(s)
    pub.verify_live.assert_called_once()


@pytest.mark.parametrize("failure", ["head", "rules", "ci", "feedback"])
def test_changed_or_failed_review_never_merges(tmp_path, monkeypatch, failure):
    s, pr, calls = setup_run(tmp_path, monkeypatch)
    if failure == "head":
        pr["head"]["sha"] = "unreviewed"
    if failure == "rules":
        pr["mergeable_state"] = "blocked"
    if failure == "ci":
        monkeypatch.setattr(pub, "require_ci_quality", Mock(side_effect=ValueError("failed")))
    if failure == "feedback":
        path = tmp_path / "production-review.json"
        review = json.loads(path.read_text())
        review["comments"] = [{"text": "change this"}]
        path.write_text(json.dumps(review))
    assert pub.run_once(s)
    assert not any(url.endswith("/merge") for url, _ in calls)
    assert (
        json.loads((tmp_path / "production-review.json").read_text())["state"]
        == "publication_needs_inspection"
    )


def test_merged_resume_does_not_merge_again(tmp_path, monkeypatch):
    s, pr, calls = setup_run(tmp_path, monkeypatch)
    pr.update(merged=True, merge_commit_sha="merged")
    assert pub.run_once(s)
    assert not any(url.endswith("/merge") for url, _ in calls)
    pub.wait_production.assert_called_once()

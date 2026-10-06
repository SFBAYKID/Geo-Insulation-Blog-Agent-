"""Remote previews require exact-commit successful CI and passing artifact scores."""

import json

import pytest

from geo_blog.remote_quality import artifact_quality


@pytest.mark.parametrize("score,passed", [(99, True), (89, False)])
def test_artifact_scores_gate_remote_review(tmp_path, monkeypatch, score, passed):
    checkout = tmp_path / "website"
    checkout.mkdir()
    folder = tmp_path / "github-quality-head"
    folder.mkdir()
    (folder / "summary.json").write_text(
        json.dumps(
            {
                "commit": "head",
                "environment": "local production build",
                "results": [
                    {
                        "route": "/blog/insulation",
                        "passed": passed,
                        "scores": {
                            "performance": score,
                            "accessibility": 100,
                            "best-practices": 100,
                            "seo": 100,
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(
        "geo_blog.remote_quality.command",
        lambda *args: json.dumps(
            [{"headSha": "head", "databaseId": 1, "status": "completed", "conclusion": "success"}]
        ),
    )
    if passed:
        assert artifact_quality(checkout, "head", "insulation")["commit"] == "head"
    else:
        with pytest.raises(ValueError):
            artifact_quality(checkout, "head", "insulation")


def test_stale_artifact_commit_is_rejected(tmp_path, monkeypatch):
    checkout = tmp_path / "website"
    checkout.mkdir()
    folder = tmp_path / "github-quality-head"
    folder.mkdir()
    (folder / "summary.json").write_text(
        json.dumps(
            {
                "commit": "other",
                "environment": "local production build",
                "results": [
                    {
                        "route": "/blog/sample",
                        "passed": True,
                        "scores": {
                            "performance": 100,
                            "accessibility": 100,
                            "best-practices": 100,
                            "seo": 100,
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(
        "geo_blog.remote_quality.command",
        lambda *args: json.dumps(
            [{"headSha": "head", "databaseId": 1, "status": "completed", "conclusion": "success"}]
        ),
    )
    with pytest.raises(ValueError, match="result failed"):
        artifact_quality(checkout, "head", "sample")


def test_failed_latest_run_cannot_reuse_older_success(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "geo_blog.remote_quality.command",
        lambda *args: json.dumps(
            [
                {
                    "headSha": "head",
                    "databaseId": 1,
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "headSha": "head",
                    "databaseId": 2,
                    "status": "completed",
                    "conclusion": "failure",
                },
            ]
        ),
    )
    with pytest.raises(ValueError, match="Latest exact-commit"):
        artifact_quality(tmp_path, "head", "sample")

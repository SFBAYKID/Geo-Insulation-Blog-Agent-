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
                ]
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

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from geo_blog import preview_pipeline as pipeline
from geo_blog.settings import Settings

URL = "https://preview.vercel.app/blog/attic-insulation/test/"


@pytest.mark.parametrize(
    "final_url,performance,error,passes,audits",
    [
        (URL, 0.90, None, True, 1),
        # A short measurement is taken again once before the gate is failed.
        (URL, 0.89, None, False, 2),
        ("https://vercel.com/login", 1, None, False, 1),
        (URL, 1, {"code": "NO_FCP"}, False, 1),
    ],
)
def test_hosted_audit_gate(tmp_path, monkeypatch, final_url, performance, error, passes, audits):
    monkeypatch.setattr(pipeline, "COLD_START_PAUSE", 0)
    settings = Settings(_env_file=None, vercel_automation_bypass_secret="private-test-value")
    store = Mock()
    store.required.return_value = {
        "payload": json.dumps(
            {"topic": {"product": {"url": "https://geo-insulation.com/services/attic-insulation/"}}}
        )
    }
    calls = []

    def run(args, cwd, log, timeout, env):
        calls.append(args)
        assert env["VERCEL_AUTOMATION_BYPASS_SECRET"] == "private-test-value"
        assert "private-test-value" not in str(args)
        if str(args[1]).endswith("audit_preview.mjs"):
            (tmp_path / "lighthouse-mobile.json").write_text(
                json.dumps(
                    {
                        "finalDisplayedUrl": final_url,
                        "runtimeError": error,
                        "categories": {
                            k: {"score": v}
                            for k, v in {
                                "performance": performance,
                                "accessibility": 0.96,
                                "best-practices": 1,
                                "seo": 0.69,
                            }.items()
                        },
                    }
                )
            )

    monkeypatch.setattr(pipeline, "command", run)
    (tmp_path / "hosted-qa.json").write_text("stale success")
    if passes:
        scores = pipeline.audit_hosted_preview(
            settings, URL, "draft", store, tmp_path, tmp_path / "log", "exact-sha"
        )
        assert scores["performance"] == 90
        assert json.loads((tmp_path / "hosted-qa.json").read_text())["sha"] == "exact-sha"
    else:
        with pytest.raises(RuntimeError):
            pipeline.audit_hosted_preview(
                settings, URL, "draft", store, tmp_path, tmp_path / "log", "exact-sha"
            )
        assert not (tmp_path / "hosted-qa.json").exists()
    assert [str(call[1]).split("/")[-1] for call in calls] == ["check_preview.mjs"] + [
        "audit_preview.mjs"
    ] * audits


def test_failed_hosted_audit_never_attaches_preview(tmp_path, monkeypatch):
    store = Mock()
    settings = Settings(_env_file=None)
    results = iter(
        [
            json.dumps([{"id": 42, "sha": "expected", "environment": "preview"}]),
            json.dumps([{"state": "success", "environment_url": "https://preview.vercel.app"}]),
        ]
    )
    monkeypatch.setattr(pipeline, "command", lambda *a, **k: next(results))
    monkeypatch.setattr(
        pipeline,
        "audit_hosted_preview",
        Mock(side_effect=RuntimeError("quality failure")),
    )
    with pytest.raises(RuntimeError, match="quality failure"):
        pipeline.wait_deployment(
            settings,
            store,
            "draft",
            {"sha": "expected", "path": "/blog/test/"},
            tmp_path,
            tmp_path,
            tmp_path / "log",
        )
    store.attach_preview.assert_not_called()
    assert not (tmp_path / "deployment.json").exists()


def test_checked_preview_resume_uses_rest_pr_update(tmp_path, monkeypatch):
    import time

    store = Mock()
    settings = Settings(_env_file=None)
    scores = {"performance": 94, "accessibility": 95, "best-practices": 100, "seo": 61}
    (tmp_path / "hosted-qa.json").write_text(
        json.dumps({"sha": "expected", "url": URL, "scores": scores, "checked_at": time.time()})
    )
    (tmp_path / "pull-request.md").write_text(
        "Review notes\nHosted mobile Lighthouse is pending; no review card is delivered until it passes.\n"
    )
    calls = []

    def command(args, *a, **k):
        calls.append(args)
        if args[1:4] == ["api", "--method", "PATCH"]:
            body = json.loads((tmp_path / "pull-request-body.json").read_text())
            assert "\n" in body["body"] and "94" in body["body"]
            return "https://github.com/SFBAYKID/geoinsulation/pull/45"
        if "statuses" in args[-1]:
            return json.dumps(
                [{"state": "success", "environment_url": "https://preview.vercel.app"}]
            )
        return json.dumps([{"id": 42, "sha": "expected", "environment": "preview"}])

    monkeypatch.setattr(pipeline, "command", command)
    audit = Mock(side_effect=AssertionError("Do not repeat same passed audit"))
    monkeypatch.setattr(pipeline, "audit_hosted_preview", audit)
    state = {
        "sha": "expected",
        "path": "/blog/attic-insulation/test/",
        "pr_url": "https://github.com/SFBAYKID/geoinsulation/pull/45",
    }
    result = pipeline.wait_deployment(
        settings, store, "draft", state, tmp_path, tmp_path, tmp_path / "log"
    )
    assert result["scores"] == scores
    assert any(a[1:4] == ["api", "--method", "PATCH"] for a in calls)
    assert not any(a[1:3] == ["pr", "edit"] for a in calls)
    store.attach_preview.assert_called_once()


def test_a_cold_first_measurement_is_taken_again_before_the_gate_decides(tmp_path, monkeypatch):
    """The same commit measured 87 cold and 95 warm on September 18, 2026."""
    monkeypatch.setattr(pipeline, "COLD_START_PAUSE", 0)
    settings = Settings(_env_file=None, vercel_automation_bypass_secret="private-test-value")
    store = Mock()
    store.required.return_value = {
        "payload": json.dumps({"topic": {"product": {"url": "https://geo-insulation.com/x/"}}})
    }
    monkeypatch.setattr(pipeline, "command", lambda *a, **k: None)
    warm = iter(
        [
            {"performance": 87, "accessibility": 100, "best-practices": 100, "seo": 61},
            {"performance": 95, "accessibility": 100, "best-practices": 100, "seo": 61},
        ]
    )

    def measure(settings, url, report_path, root, log, env):
        Path(report_path).write_text("report")
        return next(warm)

    monkeypatch.setattr(pipeline, "measure_hosted_preview", measure)
    scores = pipeline.audit_hosted_preview(
        settings, URL, "draft", store, tmp_path, tmp_path / "log", "exact-sha"
    )
    assert scores["performance"] == 95
    saved = json.loads((tmp_path / "hosted-qa.json").read_text())
    assert [m["performance"] for m in saved["measurements"]] == [87, 95]
    assert (tmp_path / "lighthouse-mobile-cold.json").exists(), (
        "the rejected cold report is kept as evidence"
    )


def test_two_failing_measurements_still_fail_the_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "COLD_START_PAUSE", 0)
    settings = Settings(_env_file=None, vercel_automation_bypass_secret="private-test-value")
    store = Mock()
    store.required.return_value = {
        "payload": json.dumps({"topic": {"product": {"url": "https://geo-insulation.com/x/"}}})
    }
    monkeypatch.setattr(pipeline, "command", lambda *a, **k: None)
    calls = []

    def measure(settings, url, report_path, root, log, env):
        Path(report_path).write_text("report")
        calls.append(url)
        return {
            "performance": 70,
            "accessibility": 100,
            "best-practices": 100,
            "seo": 61,
        }

    monkeypatch.setattr(pipeline, "measure_hosted_preview", measure)
    with pytest.raises(RuntimeError, match="quality corrections"):
        pipeline.audit_hosted_preview(
            settings, URL, "draft", store, tmp_path, tmp_path / "log", "exact-sha"
        )
    assert len(calls) == 2 and not (tmp_path / "hosted-qa.json").exists()

import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from geo_blog.pagespeed import run
from geo_blog.settings import Settings

URL = "https://preview.vercel.app/blog/test/"


def settings():
    return Settings(
        _env_file=None,
        pagespeed_api_key="google-private",
        vercel_automation_bypass_secret="preview-private",
    )


def response(final=URL, error=None):
    return {
        "lighthouseResult": {
            "finalDisplayedUrl": final,
            "requestedUrl": URL + "?x-vercel-protection-bypass=preview-private",
            "runtimeError": error,
            "categories": {
                k: {"score": v}
                for k, v in {
                    "performance": 0.91,
                    "accessibility": 0.96,
                    "best-practices": 1,
                    "seo": 0.69,
                }.items()
            },
        }
    }


def test_remote_audit_redacts_credentials_without_changing_scores(tmp_path):
    def request(req, timeout):
        assert req.get_header("X-goog-api-key") == "google-private"
        assert "google-private" not in req.full_url
        query = parse_qs(urlsplit(req.full_url).query)
        assert query["strategy"] == ["mobile"]
        assert len(query["category"]) == 4
        assert parse_qs(urlsplit(query["url"][0]).query)["x-vercel-protection-bypass"] == [
            "preview-private"
        ]
        return io.BytesIO(json.dumps(response()).encode())

    path = tmp_path / "report.json"
    report = run(settings(), URL, path, protected=True, open_url=request)
    assert report["categories"]["performance"]["score"] == 0.91
    assert "private" not in path.read_text()


@pytest.mark.parametrize(
    "final,error", [("https://vercel.com/login", None), (URL, {"code": "NO_FCP"})]
)
def test_remote_login_or_runtime_error_is_not_a_valid_article(tmp_path, final, error):
    with pytest.raises(RuntimeError):
        run(
            settings(),
            URL,
            tmp_path / "report.json",
            protected=True,
            open_url=lambda *a, **k: io.BytesIO(json.dumps(response(final, error)).encode()),
        )


def test_remote_retries_are_bounded_and_errors_hide_url_secrets(tmp_path):
    attempts = []

    def request(req, timeout):
        attempts.append(req)
        raise HTTPError(req.full_url, 429, "private details", {}, None)

    with pytest.raises(RuntimeError, match="HTTP 429") as exc:
        run(
            settings(),
            URL,
            tmp_path / "report.json",
            protected=True,
            open_url=request,
            sleep=lambda _: None,
        )
    assert len(attempts) == 5
    assert "private" not in str(exc.value)
    assert not (tmp_path / "report.json").exists()


def test_preview_secret_is_not_sent_to_lookalike_host(tmp_path):
    def unexpected(*a, **k):
        raise AssertionError("must not make a request")

    with pytest.raises(ValueError):
        run(
            settings(),
            "https://vercel.app.attacker.test/",
            tmp_path / "report.json",
            protected=True,
            open_url=unexpected,
        )

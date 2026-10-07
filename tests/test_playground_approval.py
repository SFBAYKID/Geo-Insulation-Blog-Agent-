"""Production activation must never turn playground decisions into publication."""

from unittest.mock import Mock

from geo_blog import production_publish, slack_app
from geo_blog.settings import Settings
from geo_blog.store import Store


def test_playground_approval_stays_editorial_with_production_enabled(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path,
        slack_listener_enabled=True,
        slack_bot_token="test-token",
        slack_app_token="test-app-token",
        slack_team_id="T_TEST",
        slack_approver_ids="U_TEST",
        slack_production_channel_id="C_PRODUCTION",
        production_delivery_enabled=True,
        publishing_enabled=True,
        website_preview_enabled=True,
        website_repository="Calvo-Consulting/geo-insulation",
    )
    store = Store(tmp_path)
    store.reserve("practice", "practice", "practice")
    draft = {
        "front_matter": {"title": "Practice", "description": "Practice only."},
        "report": {"word_count": 900},
        "media_status": "ready",
    }
    store.save("practice", draft)
    store.claim_delivery("practice", settings.slack_channel_id)
    store.delivered("practice", "123.456")
    actions = {}
    app = Mock()
    app.client.auth_test.return_value = {"team_id": "T_TEST", "user_id": "U_BOT"}
    app.action.side_effect = lambda name: lambda callback: actions.setdefault(name, callback)
    monkeypatch.setattr(slack_app, "App", lambda **kwargs: app)
    monkeypatch.setattr(slack_app, "SocketModeHandler", Mock())
    monkeypatch.setattr(production_publish, "start_worker", Mock())
    slack_app.serve(settings, store)
    blocks = slack_app.review_blocks(draft, "practice", settings=settings)
    assert "Playground approval never publishes" in str(blocks)
    body = {
        "team": {"id": "T_TEST"},
        "channel": {"id": settings.slack_channel_id},
        "user": {"id": "U_TEST"},
        "message": {"ts": "123.456", "blocks": blocks},
        "actions": [{"action_id": "blog_approve", "value": "practice"}],
    }
    client = Mock()
    actions["blog_approve"](Mock(), body, client)
    assert store.required("practice")["status"] == "approved"
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM publish_jobs").fetchone()[0] == 0
    assert not (tmp_path / "publication-plan.json").exists()
    assert "Playground approval never publishes" in client.chat_update.call_args.kwargs["text"]

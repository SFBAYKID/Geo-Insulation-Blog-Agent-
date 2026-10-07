"""Slack house style: one sentence per line with a blank line between, markup intact."""

from unittest.mock import Mock

from geo_blog.settings import Settings
from geo_blog.slack_guard import safe_client
from geo_blog.slack_text import sentence_lines


def test_weekly_announcement_spaces_each_sentence_on_its_own_line():
    text = (
        "<@U1> <@U2> This week's blog is ready for review: "
        "*How Does Blown-In Insulation Work? Full Guide*\n"
        "The preview and Approve button are in this thread. Approving publishes it to the "
        "website. To request changes, reply in the thread and mention me."
    )
    assert sentence_lines(text).split("\n") == [
        "<@U1> <@U2> This week's blog is ready for review: "
        "*How Does Blown-In Insulation Work? Full Guide*",
        "The preview and Approve button are in this thread.",
        "",
        "Approving publishes it to the website.",
        "",
        "To request changes, reply in the thread and mention me.",
    ]


def test_existing_sentence_lines_gain_a_blank_line_but_lists_stay_tight():
    text = "No open tasks.\nPlease add one.\n- First item.\n- Second item."
    assert sentence_lines(text) == (
        "No open tasks.\n\nPlease add one.\n- First item.\n- Second item."
    )


def test_markup_abbreviations_and_quotes_stay_intact():
    text = "See <https://x.com/a.b|the preview. Now>. Use e.g. Dr. Smith. Pay $1.50 now!"
    assert sentence_lines(text) == (
        "See <https://x.com/a.b|the preview. Now>.\n\nUse e.g. Dr. Smith.\n\nPay $1.50 now!"
    )
    assert sentence_lines("```a. B. c```\n`x. Y`") == "```a. B. c```\n`x. Y`"
    assert sentence_lines("> One. Two.") == "> One.\n> Two."
    once = sentence_lines("First. Second.")
    assert sentence_lines(once) == once == "First.\n\nSecond."


def test_guard_formats_text_and_mrkdwn_but_not_buttons():
    settings = Settings(_env_file=None)
    raw = Mock()
    safe_client(settings, raw).chat_postMessage(
        channel=settings.slack_channel_id,
        text="Saved. Thanks.",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": "One. Two."}},
            {
                "type": "actions",
                "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Go. Now"}}],
            },
        ],
    )
    sent = raw.chat_postMessage.call_args.kwargs
    assert sent["text"] == "Saved.\n\nThanks."
    assert sent["blocks"][0]["text"]["text"] == "One.\n\nTwo."
    assert sent["blocks"][1]["elements"][0]["text"]["text"] == "Go. Now"

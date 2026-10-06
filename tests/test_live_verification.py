"""Production verification rejects stale HTML, media, FAQs and sitemap evidence."""

import hashlib
import json

import httpx
import pytest

from geo_blog.live_verification import ORIGIN, verify_article


@pytest.mark.parametrize(
    "fault",
    [None, "title", "canonical", "indexing", "image", "faq", "schema", "sitemap", "redirect"],
)
def test_live_article_requires_all_reviewed_evidence(fault):
    url = ORIGIN + "/blog/attic-planning/"
    draft = {
        "markdown": "---\ntitle: Attic Planning\nslug: attic-planning\ndescription: Planning summary\nkeywords: [attic]\n---\n# Attic Planning\n\nPlan carefully.\n\n## FAQs\n\n**What comes first?**\n\nProfessional assessment.\n",
        "topic": {
            "hero": {
                "src": "/blog/media/attic-planning-hero.webp",
                "alt": "Attic",
                "caption": "Illustration: Attic.",
            }
        },
        "media_provenance": {
            "origin": "generated",
            "sha256": hashlib.sha256(b"reviewed image").hexdigest(),
        },
    }
    schema = {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "name": "What comes first?",
                "acceptedAnswer": {
                    "text": "Changed" if fault == "schema" else "Professional assessment."
                },
            }
        ],
    }
    html = '<title>Attic Planning</title><meta name="description" content="Planning summary">'
    html += f'<link rel="canonical" href="{url}"><main><article><h1>Attic Planning</h1>'
    html += '<img src="/blog/media/attic-planning-hero.webp" alt="Attic" width="1536" height="864"><p>Illustration: Attic.</p>'
    html += "<details><summary>What comes first?</summary><p>Professional assessment.</p></details></article></main>"
    html += '<script type="application/ld+json">' + json.dumps(schema) + "</script>"
    if fault == "title":
        html = html.replace("<title>Attic Planning", "<title>Old title")
    if fault == "canonical":
        html = html.replace(url, ORIGIN + "/")
    if fault == "faq":
        html = html.replace("<p>Professional assessment.", "<p>Different answer.")

    def respond(request):
        if str(request.url) == url:
            return httpx.Response(
                302 if fault == "redirect" else 200,
                text=html,
                headers={"x-robots-tag": "noindex" if fault == "indexing" else "index"},
            )
        if request.url.path.endswith(".webp"):
            return httpx.Response(200, content=b"wrong" if fault == "image" else b"reviewed image")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(
                200,
                text="<urlset><url><loc>"
                + (ORIGIN if fault == "sitemap" else url)
                + "</loc></url></urlset>",
            )
        raise AssertionError("Unexpected request")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        if fault:
            with pytest.raises(ValueError):
                verify_article(draft, client)
        else:
            assert verify_article(draft, client) == url

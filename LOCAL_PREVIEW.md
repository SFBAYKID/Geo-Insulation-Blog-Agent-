# Local Geo blog previews

Chase authorized producing and reviewing blogs locally while access to the website GitHub repository and Vercel is pending. No additional design file is needed for the initial template. This workflow never posts to Slack, publishes, completes a Basecamp task or changes the shared droplet.

## Review the first sample

Open `previews/index.html` in a browser, or use the running loopback preview at http://127.0.0.1:8766/. The first article is `previews/attic-insulation-planning-san-antonio/index.html`. All fonts, branding and artwork are local, so the files also work without the preview server. Navigation to company pages and citation links deliberately opens the actual public website.

The sample uses the existing practice brief; its topic and keywords are for design testing, not a claim that an upstream Basecamp brief or keyword plan was approved. OpenAI wrote and edited it. Deterministic checks and the AI editor passed. The hero is generated, visibly labeled Illustration:, and its saved file hash is checked before export. A local draft label, noindex metadata and an explicit unpublished status distinguish it from a live article. Local review never becomes publication approval.

## Observed design

Reference: https://geo-insulation.com/blog/signs-of-poor-insulation-san-antonio-homes/ and https://geo-insulation.com/blog/, inspected October 4, 2026.

| Token | Observed value |
| --- | --- |
| Main green | #2e7d46 |
| Dark green | #13612e |
| Link/button hover blue | #2b6cb0 |
| Heading text | #222222 |
| Body text | #3b3b3b |
| Soft background | #f7f7f7 |
| Borders | #e1e1e1 |
| Headings | Bitter; 48px desktop H1, 32px H2 |
| Body | Hind Siliguri; 18px desktop body |

The layout follows the existing contact strip, white navigation, logo, darkened image header, breadcrumb/category treatment, article column and green quote sidebar. Added local-review controls sit separately from the article. The mobile layout stacks the sidebar, provides a menu, and keeps content within the viewport. FAQs use accessible native disclosure elements and matching FAQ JSON-LD. The footer uses a compact set of verified links. This is a faithful local approximation, not a copy of the unavailable Astro source.

Logo and fonts were obtained from Geo's public site. Font licenses and source URLs are included in `geo_blog/preview_assets/`. No old blog prose, tracking scripts, contact forms or third-party map embeds are copied into the preview.

## Agent commands

Generate or resume one approved brief locally (paid OpenAI calls):

```sh
.venv/bin/python main.py local-draft config/practice-brief.json
```

Render a saved, editorially approved payload without new model calls:

```sh
.venv/bin/python main.py local-preview storage/local-design-sample/payload.json
```

Serve only the exported preview directory, never the project or its credentials:

```sh
.venv/bin/python -m http.server 8766 --bind 127.0.0.1 --directory previews
```

Each article includes `index.html`, `hero.webp`, `article.md`, `article.json` and a library summary. The JSON contains portable content and metadata, not provider transcripts or local credential paths. Drafts and generated images are ignored by Git. Template code and public brand assets can be versioned in the agent repository.

## Later publishing integration

Once access is available, map the portable article, FAQs and imagery into the actual Astro content files. Confirm staging branch, routes, canonical metadata, author conventions, related-article behavior and Vercel project. Then run the exact-commit build, mobile/performance checks and test-channel approval flow. Only after those checks should production publishing and Basecamp completion be enabled. Connecting accounts alone is not an end-to-end publishing verification.

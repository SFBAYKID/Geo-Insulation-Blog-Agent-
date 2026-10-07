# Geo Insulation Blog Agent

Writes from supplied Basecamp briefs, checks prose with an AI editor, attaches labeled OpenAI illustrations, and prepares Slack review. Intended publication uses an exact-version Slack approval, a website GitHub PR, verified Vercel publication and then Basecamp completion.

**Weekly production reviews are enabled for Wednesday 9 AM Pacific in C0BP597644B.**
The Geo droplet listener uses verified dedicated Basecamp and GitHub credentials.
The full illustrated playground review and real approval button passed; playground approval never publishes.
Website foundation PR #87 is deployed, and each new article still requires its own exact-version production Slack approval.
The Basecamp brief queue is empty, so no new article can be produced yet.
Upstream keyword access and the separate Airtable/Google worker remain outstanding.
A real production article publication and subsequent Basecamp completion have not yet been exercised.
See SETUP-PROGRESS.md.

Agent repository: SFBAYKID/Geo-Insulation-Blog-Agent- (trailing hyphen). This is not the website repository. Website: https://geo-insulation.com/. Basecamp account 5395893, Geo project 40851698. The dedicated Geo Blog Briefs list (10378779880) currently has no open briefs; existing Content Sprint optimization tasks are not an authorized replacement for the weekly new-blog queue.

## Local setup and checks

Use Python 3.11+. Keep private settings in .env.local, mode 0600. A separate .env.example contains no credentials. SLACK_LISTENER_ENABLED=false locally. PROVIDER model names need access verification before paid work.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r deploy/agent-requirements.txt -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/ruff check geo_blog tools tests main.py
.venv/bin/ruff format --check geo_blog tools tests main.py
.venv/bin/mypy
.venv/bin/python -m tools.check_project
.venv/bin/python main.py doctor
```

Once prerequisites are verified, `main.py basecamp-queue` reads the queue; `main.py practice config/practice-brief.json` writes without Slack; `main.py weekly --test` prepares a checked review in the playground. Paid commands require explicit configured provider access. Test Approve never publishes.

## Recovery and state

Private storage includes SQLite reservations, draft evidence, provider receipts, media history and exact-version reviews. Never overwrite server state with development state. A crash or ambiguous external action requires reconciliation before retrying. Chase asks the agent to fix failures; the agent handles repairs and safe reruns.

All automated Slack output passes through slack_guard and sentence_lines. Only finished blogs with imagery reach the client. Upstream people supply research and keywords; this agent writes, edits and publishes after approval.

## Model provider

Chase selected OpenAI only. OPENAI_API_KEY powers writing, the AI editor, Slack conversation and images; no Anthropic key is required. The initial budget text model is gpt-6-luna, with the same structural and editorial gates. openai_client.py translates saved requests to the Responses API; model_response.py preserves text, tool calls and provider citations for resumable work. Output truncation and refusals fail closed. Requests have no automatic retries; existing call budgets and reconciliation still apply. Model quality must pass the full test before production.

## Local previews while publishing access is pending

Use `main.py local-draft config/practice-brief.json` to generate and resume a local illustrated draft. Use `main.py local-preview storage/local-design-sample/payload.json` to render saved approved content without paid calls. Open `previews/index.html` for the local draft library. See [LOCAL_PREVIEW.md](LOCAL_PREVIEW.md) for the observed brand palette, review files and later integration steps. These commands do not use Slack, mutate Basecamp or publish.

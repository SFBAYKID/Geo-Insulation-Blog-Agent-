"""Join the photo inventory, observations and reviewed derivatives for browsing."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from tools.keyword_sync import save


def export() -> dict[str, Any]:
    """Write a private full catalog, preserving suggestions separately from approvals."""
    root = Path("storage/drive-catalog")
    analysis_root = Path("storage/photo-analysis")
    inventory = json.loads((root / "inventory.json").read_text())
    keywords = json.loads((analysis_root / "keywords.json").read_text())
    manifest = json.loads(Path("config/image-catalog.json").read_text())
    prepared = {p["drive_file_id"]: p for p in manifest["images"]}
    records = []
    rows = []
    for source in inventory["files"]:
        receipt = json.loads((analysis_root / "analysis" / (source["id"] + ".json")).read_text())
        observation = receipt["analysis"]
        topics = observation["suggested_topics"]
        photo = prepared.get(source["id"])
        record = dict(source)
        record.update(
            {
                "visual_analysis_status": "analyzed",
                "analysis": observation,
                "analysis_model": receipt["model"],
                "analysis_prompt_version": receipt["prompt_version"],
                "suggested_keyword_record_ids": [
                    k["id"] for k in keywords if k["fields"].get("Topic") in topics
                ],
                "reviewed_derivative": photo,
                "publication_permission": photo["publication_permission"]
                if photo
                else receipt.get("publication_permission", "unknown"),
                "privacy_review": photo["privacy_review"] if photo else "pending",
            }
        )
        records.append(record)
        cells = [
            source["folder_path"].removeprefix("Media/"),
            source["name"],
            observation["factual_description"],
            ", ".join(topics),
            "; ".join(observation["privacy_concerns"]) or "Final review required",
            "Prepared; " + record["publication_permission"] if photo else "Not prepared",
        ]
        escaped = [html.escape(c) for c in cells]
        escaped[1] = (
            '<a target="_blank" rel="noopener" href="https://drive.google.com/file/d/'
            + html.escape(source["id"], quote=True)
            + '/view">'
            + escaped[1]
            + "</a>"
        )
        if photo:
            escaped[2] = html.escape(photo["factual_description"])
            escaped[4] = "Final derivative visually reviewed"
        rows.append("<tr>" + "".join("<td>" + c + "</td>" for c in escaped) + "</tr>")
    output = {
        "schema_version": 1,
        "kind": "drive_visual_catalog",
        "images": records,
        "root_folder_id": inventory["root_folder_id"],
        "exact_duplicate_groups": inventory["exact_duplicate_groups"],
    }
    save(root / "catalog.json", output)
    page = (
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Geo visual photo catalog</title><style>
body{font:16px system-ui;max-width:1500px;margin:36px auto;padding:0 24px;background:#f8fafc;color:#17202c}
p{line-height:1.5}table{border-collapse:collapse;background:white;width:100%}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd;vertical-align:top}th{background:#e2e8f0}td:nth-child(3){min-width:260px}input{font:inherit;padding:12px;width:70%;margin-bottom:20px}a{color:#175ea8}
</style><h1>Geo Insulation visual photo catalog</h1><p>Prepared source inventory.
Descriptions and suggested topic matches are recorded for every photo.
Suggestions are separate from reviewed keyword matches. Originals remain in Drive.</p>
<p>Derivative review and publication permission
is tracked separately in the runtime manifest; no approval is inferred from Drive access.</p>
<label for="q">Search descriptions, subjects or folders</label><br><input id="q" type="search">
<table><thead><tr><th>Folder</th><th>Source photo</th><th>Description</th><th>Suggested topics</th>
<th>Privacy</th><th>Preparation</th></tr></thead><tbody>"""
        + "".join(rows)
        + """</tbody></table>
<script>document.querySelector('#q').addEventListener('input',e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q));});</script></html>"""
    )
    (root / "catalog.html").write_text(page)
    return {
        "described": len(records),
        "prepared": len(prepared),
        "privacy_flagged": sum(bool(r["analysis"]["privacy_concerns"]) for r in records),
    }


if __name__ == "__main__":
    print(json.dumps(export()))

# Geo keyword source

Chase authorized a separate Geo Airtable base and Keywords table. Creation is pending account/workspace access and the source keyword file. Do not invent keywords or import unrelated customer data.

Use Keyword, Topic, Target Page, Notes, Client Notes, On-Page Target, Priority and Search Volume fields as supported by keyword_catalog.py. Preserve stable record IDs, view order and distinct page targets. Confirm source schema before creating mappings. Blog adapter uses a token scoped only to Geo with read-only access and AIRTABLE_WRITE_ENABLED=false.

A separate authorized sync credential may write only mapped source fields. Publication fields must not be overwritten by keyword sync. Do not mark service pages complete for related blog publication.

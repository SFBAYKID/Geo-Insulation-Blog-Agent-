# Geo image contract

Runtime catalog schema: geo_blog.media_catalog.Catalog, schema_version 1 and images array. config/image-catalog.json starts empty. Each image needs stable id/drive_file_id, derivative_path under assets/blog, exact SHA-256, decoded width/height, factual_description, optional caption, keyword_record_ids, relevance_tags, publication_permission, privacy_review, metadata_stripped and origin. Generated assets require a nonempty fallback_reason and Illustration: caption.

Selection requires approved permission, cleared privacy, stripped metadata, correct checksum/dimensions, permitted path and a relevant task/keyword match. Images must be 640–4096 pixels wide and 360–4096 high, at most 1 MB. Accepted derivatives are validated WebP or metadata-stripped JPEG. Never infer contents or location from keywords. Unique filenames map to permanent website media.

Chase selected OpenAI illustrations. Respect IMAGE_GENERATION_ENABLED and a configured key before paid calls. Generated imagery cannot be described as customer work or before/after evidence. Every delivered finished blog needs media; missing imagery blocks delivery. Future real photos require source permission and final visual review. config/blog-task-images.json is an empty consumer mapping, not a permission grant.

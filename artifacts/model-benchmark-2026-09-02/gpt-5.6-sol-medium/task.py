"""Small deterministic benchmark task."""


def normalize_tags(tags):
    """Return normalized tags while preserving their first-seen order."""
    normalized = []
    seen = set()

    for tag in tags:
        tag = tag.strip().lower()
        if tag and tag not in seen:
            seen.add(tag)
            normalized.append(tag)

    return normalized

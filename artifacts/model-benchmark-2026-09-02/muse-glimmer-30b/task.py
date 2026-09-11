"""Small deterministic benchmark task."""


def normalize_tags(tags):
    """Return normalized tags while preserving their first-seen order."""
    seen = set()
    result = []
    for tag in tags:
        normalized = tag.strip().lower()
        if not normalized:
            continue
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result

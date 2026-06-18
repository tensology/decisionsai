"""Pixazo model catalog for Settings → LLMs media dropdowns."""

from distr.core.pixazo_client import pixazo_models_for_modality


def get_pixazo_models(modality: str | None = None):
    """Return [{id, name, output_modalities}] for UI model pickers."""
    rows = pixazo_models_for_modality(modality)
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "output_modalities": list(row.get("output_modalities") or []),
        }
        for row in rows
    ]

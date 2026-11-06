"""Read local Gemini settings while honoring hosted environment variables."""

import os
from pathlib import Path

from dotenv import dotenv_values


def gemini_settings():
    local = dotenv_values(Path(__file__).with_name(".env"), interpolate=False)
    key = os.getenv("GEMINI_API_KEY") or local.get("GEMINI_API_KEY") or ""
    model = os.getenv("GEMINI_MODEL") or local.get("GEMINI_MODEL") or ""
    return key.strip(), model.strip() or None

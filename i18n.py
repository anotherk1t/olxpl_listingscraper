import json
import os
import logging

logger = logging.getLogger(__name__)

LOCALES = {}

def load_locales():
    """Load all JSON files from the locales directory."""
    locales_dir = os.path.join(os.path.dirname(__file__), "locales")
    if not os.path.isdir(locales_dir):
        logger.warning(f"Locales directory not found at {locales_dir}")
        return

    for filename in os.listdir(locales_dir):
        if filename.endswith(".json"):
            lang_code = filename[:-5]
            try:
                with open(os.path.join(locales_dir, filename), "r", encoding="utf-8") as f:
                    LOCALES[lang_code] = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load locale {filename}: {e}")

# Load immediately on import
load_locales()

def get_text(lang: str, key: str, **kwargs) -> str:
    """
    Get a translated string for a given language code and key.
    Falls back to English ('en') if language or key is missing.
    Interpolates kwargs into the string using .format().
    """
    if lang not in LOCALES:
        lang = "en"
    
    text = LOCALES.get(lang, {}).get(key)
    
    if text is None:
        # Fallback to english
        text = LOCALES.get("en", {}).get(key, key)
    
    if kwargs:
        try:
            return text.format(**kwargs)
        except KeyError as e:
            logger.warning(f"Missing format key {e} for translation key '{key}' in lang '{lang}'")
            return text
    return text

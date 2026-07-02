"""Static constants and paths for the Whisper Dart app."""

from pathlib import Path

APP_DIR        = Path(__file__).parent
PROJECT_ROOT   = APP_DIR.parent
LOG_DIR        = APP_DIR / "logs"
LOG_FILE       = LOG_DIR / "whisperdart.log"   # all levels (INFO+)
ERR_FILE       = LOG_DIR / "whisperdart.err"   # warnings + errors only
DB_FILE        = APP_DIR / "whisper.db"
RECORDING_STORAGE = PROJECT_ROOT / "recording-storage"
DEFAULT_SAVE   = str(Path.home() / "Desktop")

# Sentinel project ids
INBOX_ID = "__inbox__"
ALL_ID   = "__all__"

# Whisper CLI options
MODELS = [
    "turbo", "large-v3-turbo", "large-v3", "large-v2", "large-v1",
    "medium", "medium.en", "small", "small.en",
    "base", "base.en", "tiny", "tiny.en",
]
MODEL_HINTS = {
    "turbo":          "Fast, multilingual (~8× speed)",
    "large-v3-turbo": "Recommended — large accuracy + fast (~8× speed)",
    "large-v3":       "Best accuracy, slowest",
    "large-v2":       "Large accuracy, slow",
    "large-v1":       "Large accuracy, slow",
    "medium":         "Balanced, multilingual",
    "medium.en":      "Balanced, English-only",
    "small":          "Fast, multilingual",
    "small.en":       "Fast, English-only",
    "base":           "Very fast, multilingual",
    "base.en":        "Very fast, English-only",
    "tiny":           "Fastest, multilingual",
    "tiny.en":        "Fastest, English-only",
}
LANGUAGES = [
    "Auto-detect",
    "Afrikaans", "Albanian", "Amharic", "Arabic", "Armenian", "Assamese",
    "Azerbaijani", "Bashkir", "Basque", "Belarusian", "Bengali", "Bosnian",
    "Breton", "Bulgarian", "Burmese", "Cantonese", "Catalan", "Chinese",
    "Croatian", "Czech", "Danish", "Dutch", "English", "Estonian", "Faroese",
    "Finnish", "French", "Galician", "Georgian", "German", "Greek", "Gujarati",
    "Haitian Creole", "Hausa", "Hawaiian", "Hebrew", "Hindi", "Hungarian",
    "Icelandic", "Indonesian", "Italian", "Japanese", "Javanese", "Kannada",
    "Kazakh", "Khmer", "Korean", "Lao", "Latin", "Latvian", "Lingala",
    "Lithuanian", "Luxembourgish", "Macedonian", "Malagasy", "Malay", "Malayalam",
    "Maltese", "Maori", "Marathi", "Mongolian", "Nepali", "Norwegian", "Occitan",
    "Pashto", "Persian", "Polish", "Portuguese", "Punjabi", "Romanian", "Russian",
    "Sanskrit", "Serbian", "Shona", "Sindhi", "Sinhala", "Slovak", "Slovenian",
    "Somali", "Spanish", "Sundanese", "Swahili", "Swedish", "Tagalog", "Tajik",
    "Tamil", "Tatar", "Telugu", "Thai", "Tibetan", "Turkish", "Turkmen",
    "Ukrainian", "Urdu", "Uzbek", "Vietnamese", "Welsh", "Yiddish", "Yoruba",
]
OUTPUT_FORMATS = ["all", "txt", "srt", "vtt", "tsv", "json"]

# Sidebar accent colour (referenced by styles.css via {{PURPLE}} placeholder).
PURPLE = "#5B4FE6"

# Colour palette for auto-assigning tag colours (stable per tag name).
TAG_PALETTE = [
    "#5B4FE6", "#F59E0B", "#10B981", "#EF4444", "#3B82F6",
    "#8B5CF6", "#EC4899", "#14B8A6", "#F97316", "#6B7280",
]

# Sentinel for meetings_html `selected_id` meaning "clear selection in JS".
SELECT_NONE = "__none__"

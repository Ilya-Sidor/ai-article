import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("AI_ARTICLE_DATA_DIR", ROOT_DIR / "data"))
FRONTEND_DIR = ROOT_DIR / "frontend"

# PRD 8.3: the reasoning model plans/interprets, a lighter model parses and extracts.
MODEL_REASONING = os.environ.get("AI_ARTICLE_MODEL_REASONING", "claude-opus-5-5")
MODEL_EXTRACTION = os.environ.get("AI_ARTICLE_MODEL_EXTRACTION", "claude-sonnet-5")

ANALYSIS_SEED = int(os.environ.get("AI_ARTICLE_SEED", "20260927"))
ANALYSIS_TIMEOUT_S = int(os.environ.get("AI_ARTICLE_ANALYSIS_TIMEOUT", "600"))
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


ARTICLE_TYPES = {
    "original_article": "Original article",
    "case_series": "Case series",
    "brief_report": "Brief report",
    "case_report": "Case report (n = 1)",
}

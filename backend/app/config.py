"""Central configuration. Everything can be overridden with environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load backend/.env (KEY=value lines) without overriding real environment variables.
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
DATA_DIR = Path(os.getenv("DS_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "disputesaathi.db"
PACK_DIR = DATA_DIR / "packs"
UPLOAD_DIR = DATA_DIR / "uploads"
SAMPLE_DIR = DATA_DIR / "samples"
RULES_FILE = BASE_DIR / "app" / "rules.yaml"

for d in (DATA_DIR, PACK_DIR, UPLOAD_DIR, SAMPLE_DIR):
    d.mkdir(parents=True, exist_ok=True)

PORT = int(os.getenv("DS_PORT") or os.getenv("PORT") or "8000")  # hosts like Render set PORT
SELF_URL = os.getenv("DS_SELF_URL", f"http://127.0.0.1:{PORT}")

# Shared secret used to sign webhook calls from the (mock) Paytm system.
WEBHOOK_SECRET = os.getenv("DS_WEBHOOK_SECRET", "paytm-mock-webhook-secret")
# API key DisputeSaathi uses when calling the (mock) Paytm Dispute API.
PAYTM_API_KEY = os.getenv("DS_PAYTM_API_KEY", "mock-merchant-api-key")

# LLM settings. DS_LLM_PROVIDER = auto | claude | groq | offline.
# "auto" picks Claude if an Anthropic key is set, else Groq if a Groq key is set, else offline.
# Offline = rule-based fallback, so the demo never depends on the internet.
LLM_PROVIDER = os.getenv("DS_LLM_PROVIDER", "auto").lower()
LLM_MODEL = os.getenv("DS_LLM_MODEL", "claude-opus-5-5")          # Claude model
LLM_EFFORT = os.getenv("DS_LLM_EFFORT", "low")
GROQ_MODEL = os.getenv("DS_GROQ_MODEL", "openai/gpt-oss-120b")     # Groq tool-calling model (evidence loop)
GROQ_FAST_MODEL = os.getenv("DS_GROQ_FAST_MODEL", "openai/gpt-oss-20b")  # Groq model for reading + writing
# Free-tier limits are per model (e.g. 8,000 tokens/min), so spreading work across models helps.
GROQ_VISION_MODEL = os.getenv("DS_GROQ_VISION_MODEL", "qwen/qwen3.8-27b")  # Groq model that reads bill photos ("" = off)
FORCE_OFFLINE = os.getenv("DS_OFFLINE", "0") == "1"

# Pause between agent steps so judges can watch the live trace (seconds).
STEP_DELAY = float(os.getenv("DS_STEP_DELAY", "0.7"))

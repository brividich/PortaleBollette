"""
Impostazioni Django del tool di analisi bollette.

Scaffold minimo (greenfield) creato per ospitare il modello dati della FASE 2.
Le configurazioni del Bridge Home Assistant (ADR-002) sono lette SOLO da
variabili d'ambiente: nessun segreto e nessun URL hardcoded.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    """Legge una variabile d'ambiente booleana ('True'/'1'/'yes' = vero)."""
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "si", "on"}


# --- Sicurezza / base Django ---
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = _env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "bollette",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "it-it"
TIME_ZONE = "Europe/Rome"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Bridge Home Assistant (ADR-002) — TUTTO da variabili d'ambiente.
# ---------------------------------------------------------------------------
HA_BASE_URL = os.environ.get("HA_BASE_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
HA_TLS_VERIFY = _env_bool("HA_TLS_VERIFY", True)
HA_CA_BUNDLE = os.environ.get("HA_CA_BUNDLE", "") or None
# Modalita di calcolo del prezzo pubblicato: 'ultima_bolletta' | 'media_mobile_12m'
PREZZO_HA_MODE = os.environ.get("PREZZO_HA_MODE", "media_mobile_12m")
# Entity dei kWh consumati per la validazione (DA SCOPRIRE/confermare in HA).
HA_ENTITY_KWH_CONSUMO = os.environ.get("HA_ENTITY_KWH_CONSUMO", "")
# Entity dei Smc gas per validazione in HA.
HA_ENTITY_SMC_CONSUMO = os.environ.get("HA_ENTITY_SMC_CONSUMO", "")
# Entity per prezzo gas su HA
HA_ENTITY_PREZZO_GAS = os.environ.get("HA_ENTITY_PREZZO_GAS", "input_number.prezzo_gas_smc")
# Entity sensore PUN su HA (opzionale, es. pun_sensor)
HA_ENTITY_PUN = os.environ.get("HA_ENTITY_PUN", "sensor.pun_mono")
# Se True, pubblica automaticamente il prezzo in HA a fine ingest bolletta.
HA_SYNC_AUTO = _env_bool("HA_SYNC_AUTO", False)

# ---------------------------------------------------------------------------
# Open-Meteo API (Gradi Giorno / Normalizzazione Climatica)
# ---------------------------------------------------------------------------
METEO_LATITUDE = float(os.environ.get("METEO_LATITUDE", "41.9028"))
METEO_LONGITUDE = float(os.environ.get("METEO_LONGITUDE", "12.4964"))

# ---------------------------------------------------------------------------
# Pipeline Ingest PDF & Ollama LLM (ADR-001)
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:latest")


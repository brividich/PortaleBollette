"""
Client per l'API esterna Open-Meteo (https://open-meteo.com).
Completamente gratuito, nessun token di autenticazione richiesto.

Fornisce:
- Temperatura media del periodo di fatturazione.
- Calcolo dei Gradi Giorno di Riscaldamento (HDD - Heating Degree Days, base 18°C)
  per normalizzare i consumi di gas e riscaldamento rispetto alla rigidità dell'inverno.
- Calcolo dei Gradi Giorno di Raffrescamento (CDD - Cooling Degree Days, base 24°C)
  per climatizzatori / pompe di calore estive.

Fail-safe: non solleva mai eccezioni che bloccano l'ingest di una bolletta.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import requests
from django.conf import settings

logger = logging.getLogger("bollette.meteo")

_TIMEOUT = 8  # secondi


def calcola_gradi_giorno(
    inizio: date,
    fine: date,
    latitudine: float | None = None,
    longitudine: float | None = None,
    base_hdd: float = 18.0,
    base_cdd: float = 24.0,
) -> dict | None:
    """
    Interroga Open-Meteo per il periodo [inizio, fine] e calcola:
    - hdd: Gradi Giorno di Riscaldamento (base 18°C) = sum(max(0, 18 - T_media))
    - cdd: Gradi Giorno di Raffrescamento (base 24°C) = sum(max(0, T_media - 24))
    - t_media: Temperatura media del periodo
    - giorni_rilevati: Numero di giorni con dati validi

    Ritorna un dizionario con valori Decimal a 2 cifre, oppure None in caso di errore.
    """
    if latitudine is not None:
        lat = latitudine
    else:
        try:
            from .models import ConfigurazioneSistema
            lat = float(ConfigurazioneSistema.get_config().meteo_latitude)
        except Exception:
            lat = float(getattr(settings, "METEO_LATITUDE", 41.9028))

    if longitudine is not None:
        lon = longitudine
    else:
        try:
            from .models import ConfigurazioneSistema
            lon = float(ConfigurazioneSistema.get_config().meteo_longitude)
        except Exception:
            lon = float(getattr(settings, "METEO_LONGITUDE", 12.4964))


    start_iso = inizio.isoformat()
    end_iso = fine.isoformat()

    # Scegli endpoint: archive per date passate, forecast per date recenti / correnti
    oggi = date.today()
    delta_giorni = (oggi - fine).days

    # Se la data di fine è più recente di 5 giorni fa, usiamo forecast con fallback ad archive
    urls = []
    if delta_giorni < 7:
        urls.append(("https://api.open-meteo.com/v1/forecast", {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_iso,
            "end_date": end_iso,
            "daily": "temperature_2m_mean",
            "timezone": "Europe/Rome",
        }))
    urls.append(("https://archive-api.open-meteo.com/v1/archive", {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_iso,
        "end_date": end_iso,
        "daily": "temperature_2m_mean",
        "timezone": "Europe/Rome",
    }))

    temperature: list[float] = []
    ultimo_errore = None

    for url, params in urls:
        try:
            resp = requests.get(url, params=params, timeout=_TIMEOUT)
            if resp.status_code == 200:
                dati = resp.json()
                daily = dati.get("daily", {})
                raw_temp = daily.get("temperature_2m_mean", [])
                temperature = [float(t) for t in raw_temp if t is not None]
                if temperature:
                    break
            else:
                ultimo_errore = f"HTTP {resp.status_code}: {resp.text[:100]}"
        except Exception as exc:
            ultimo_errore = f"{type(exc).__name__}: {exc}"

    if not temperature:
        logger.warning(
            "Open-Meteo: nessun dato di temperatura per %s -> %s (lat: %s, lon: %s): %s",
            start_iso, end_iso, lat, lon, ultimo_errore
        )
        return None

    # Calcolo HDD e CDD
    hdd_totale = sum(max(0.0, base_hdd - t) for t in temperature)
    cdd_totale = sum(max(0.0, t - base_cdd) for t in temperature)
    t_media = sum(temperature) / len(temperature)

    def _d2(val: float) -> Decimal:
        return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "hdd": _d2(hdd_totale),
        "cdd": _d2(cdd_totale),
        "t_media": _d2(t_media),
        "giorni_rilevati": len(temperature),
        "latitudine": lat,
        "longitudine": lon,
    }

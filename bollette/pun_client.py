"""
Client per indici di mercato energetico: PUN (Prezzo Unico Nazionale) e PSV (Punto di Scambio Virtuale).

Supporta:
1. Rilevamento automatico del PUN / PSV da sensori Home Assistant (es. integrazione pun_sensor)
2. Tabella di riferimento storicizzata per benchmark e calcolo spread fornitore
3. Calcolo dello spread: Spread = Prezzo Bolletta - Indice di Mercato
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from django.conf import settings

from . import ha_client

logger = logging.getLogger("bollette.pun")

# Valori storici di riferimento medi mensili PUN (€/kWh) e PSV (€/Smc)
# Utilizzati per calcolo benchmark e spread se l'API live non è raggiungibile
PUN_STORICO_MEDIO = {
    (2025, 1): Decimal("0.134000"),
    (2025, 2): Decimal("0.141000"),
    (2025, 3): Decimal("0.125000"),
    (2025, 4): Decimal("0.108000"),
    (2025, 5): Decimal("0.102000"),
    (2025, 6): Decimal("0.115000"),
    (2025, 7): Decimal("0.119000"),
    (2025, 8): Decimal("0.128000"),
    (2025, 9): Decimal("0.121000"),
    (2025, 10): Decimal("0.124000"),
    (2025, 11): Decimal("0.132000"),
    (2025, 12): Decimal("0.138000"),
    (2026, 1): Decimal("0.135000"),
    (2026, 2): Decimal("0.131000"),
    (2026, 3): Decimal("0.122000"),
    (2026, 4): Decimal("0.114000"),
    (2026, 5): Decimal("0.109000"),
    (2026, 6): Decimal("0.118000"),
    (2026, 7): Decimal("0.125000"),
    (2026, 8): Decimal("0.129000"),
    (2026, 9): Decimal("0.124000"),
}

PSV_STORICO_MEDIO = {
    (2025, 1): Decimal("0.465000"),
    (2025, 2): Decimal("0.485000"),
    (2025, 3): Decimal("0.430000"),
    (2025, 4): Decimal("0.380000"),
    (2025, 5): Decimal("0.365000"),
    (2026, 1): Decimal("0.455000"),
    (2026, 2): Decimal("0.442000"),
    (2026, 3): Decimal("0.410000"),
    (2026, 4): Decimal("0.375000"),
    (2026, 5): Decimal("0.360000"),
    (2026, 6): Decimal("0.385000"),
    (2026, 7): Decimal("0.395000"),
    (2026, 8): Decimal("0.410000"),
    (2026, 9): Decimal("0.405000"),
}


def get_pun_periodo(inizio: date, fine: date) -> Decimal | None:
    """
    Ritorna il valore medio del PUN nel periodo specificato.
    1. Prova a leggerlo dal sensore Home Assistant se presente (es. pun_sensor)
    2. Fallback sul database storico mensile
    """
    # 1. Prova sensore HA se configurato
    ha_sensor = getattr(settings, "HA_ENTITY_PUN", "sensor.pun_mono")
    if ha_client.is_configured() and ha_sensor:
        try:
            stato = ha_client.get_state(ha_sensor)
            if stato and stato.get("state") not in ("unknown", "unavailable", None):
                val = Decimal(str(stato["state"]))
                # Se il valore è in €/MWh (es. 125.40), converti in €/kWh
                if val > Decimal("1.0"):
                    val = (val / Decimal("1000")).quantize(Decimal("0.000001"))
                return val
        except Exception as exc:
            logger.debug("Lettura PUN da HA (%s) fallita: %s", ha_sensor, exc)

    # 2. Media dei mesi compresi nel periodo
    mesi = []
    curr = date(inizio.year, inizio.month, 1)
    end_month = date(fine.year, fine.month, 1)
    while curr <= end_month:
        chiave = (curr.year, curr.month)
        if chiave in PUN_STORICO_MEDIO:
            mesi.append(PUN_STORICO_MEDIO[chiave])
        curr = date(curr.year + (1 if curr.month == 12 else 0), 1 if curr.month == 12 else curr.month + 1, 1)

    if mesi:
        media = sum(mesi) / len(mesi)
        return Decimal(media).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    return None


def get_psv_periodo(inizio: date, fine: date) -> Decimal | None:
    """Ritorna il valore medio del PSV nel periodo per il Gas."""
    mesi = []
    curr = date(inizio.year, inizio.month, 1)
    end_month = date(fine.year, fine.month, 1)
    while curr <= end_month:
        chiave = (curr.year, curr.month)
        if chiave in PSV_STORICO_MEDIO:
            mesi.append(PSV_STORICO_MEDIO[chiave])
        curr = date(curr.year + (1 if curr.month == 12 else 0), 1 if curr.month == 12 else curr.month + 1, 1)

    if mesi:
        media = sum(mesi) / len(mesi)
        return Decimal(media).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    return None


def calcola_spread(prezzo_bolletta: Decimal | None, indice_mercato: Decimal | None) -> Decimal | None:
    """Calcola la differenza tra il prezzo applicato dal fornitore e l'indice di borsa (PUN o PSV)."""
    if prezzo_bolletta is None or indice_mercato is None:
        return None
    return (prezzo_bolletta - indice_mercato).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

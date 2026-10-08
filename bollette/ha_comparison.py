"""
Modulo di Confronto e Validazione tra Bollette e Home Assistant.

Supporta due canali di acquisizione delle misure reali:
1. Polling automatico dalla rete via REST API di Home Assistant (/api/history/period).
2. Caricamento manuale di file CSV esportati da Home Assistant (Energy Dashboard o Developer Tools > Statistics).

Calcola:
- Scostamento assoluto (kWh / Smc) e percentuale (%)
- Classificazione di affidabilità (Congruo, Discrepanza Moderata, Anomalia)
- Impatto economico stimato (€ pagati in eccesso o in difetto rispetto al contatore privato)
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.utils import timezone

from . import ha_client
from .models import BollettaElettrica, BollettaGas, ConfigurazioneSistema

logger = logging.getLogger("bollette.ha_confronto")


# ===========================================================================
# 1. POLLING AUTOMATICO VIA REST API
# ===========================================================================
def esegui_polling_confronto(tipo: str = "tutti", bolletta_id: int | None = None) -> dict[str, Any]:
    """
    Interroga Home Assistant via REST API per verificare le bollette archiviate.
    Aggiorna i campi kwh_misurati_ha / smc_misurati_ha e scostamento_ha_pct sui modelli.
    """
    cfg = ConfigurazioneSistema.get_config()
    entity_luce = cfg.ha_entity_kwh_consumo
    entity_gas = cfg.ha_entity_smc_consumo

    if not ha_client.is_configured():
        return {
            "ok": False,
            "errore": "Home Assistant non configurato. Inserisci URL e Long-Lived Token nelle Configurazioni.",
            "aggiornati": 0,
        }

    if not entity_luce and not entity_gas:
        return {
            "ok": False,
            "errore": "Nessun sensore di consumo configurato. Vai in Configurazioni e imposta il tuo 'Sensore Consumo Elettrico (kWh)' o 'Sensore Consumo Gas (Smc)'.",
            "aggiornati": 0,
        }

    if tipo == "luce" and not entity_luce:
        return {
            "ok": False,
            "errore": "Sensore consumo elettrico (kWh) non configurato. Vai in Configurazioni e seleziona il tuo sensore luce da Home Assistant.",
            "aggiornati": 0,
        }

    if tipo == "gas" and not entity_gas:
        return {
            "ok": False,
            "errore": "Sensore consumo gas (Smc) non configurato. Vai in Configurazioni e seleziona il tuo sensore gas da Home Assistant.",
            "aggiornati": 0,
        }

    aggiornati = 0
    dettagli = []
    tz = timezone.get_current_timezone()

    # 1. Bollette Luce
    if tipo in ("tutti", "luce") and entity_luce:
        qs_luce = BollettaElettrica.objects.all().order_by("-periodo_fine")
        if bolletta_id:
            qs_luce = qs_luce.filter(pk=bolletta_id)

        for b in qs_luce:
            dt_inizio = timezone.make_aware(datetime.combine(b.periodo_inizio, datetime.min.time()), tz)
            dt_fine = timezone.make_aware(datetime.combine(b.periodo_fine, datetime.max.time()), tz)

            try:
                lettura = ha_client.get_energia_consumata(entity_luce, dt_inizio, dt_fine)
            except Exception as exc:
                logger.warning("Errore polling HA luce per %s: %s", b, exc)
                lettura = None

            if lettura and lettura.get("delta") is not None and lettura["delta"] > 0:
                kwh_ha = Decimal(str(round(lettura["delta"], 2)))
                kwh_fatt = Decimal(str(b.kwh_fatturati or 0))

                scost_pct = None
                if kwh_ha > 0:
                    scost_pct = ((kwh_fatt - kwh_ha) / kwh_ha * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                b.kwh_misurati_ha = kwh_ha
                b.scostamento_ha_pct = scost_pct
                b.fonte_misura_ha = f"Polling REST ({entity_luce})"
                b.save(update_fields=["kwh_misurati_ha", "scostamento_ha_pct", "fonte_misura_ha"])

                aggiornati += 1
                dettagli.append({
                    "id": b.pk,
                    "tipo": "luce",
                    "bolletta": str(b),
                    "fatturato": f"{kwh_fatt} kWh",
                    "misurato_ha": f"{kwh_ha} kWh",
                    "scostamento_pct": float(scost_pct) if scost_pct else 0.0,
                    "stato": "congruo" if abs(scost_pct or 0) <= 3 else ("moderato" if abs(scost_pct or 0) <= 8 else "anomalo"),
                })

    # 2. Bollette Gas
    if tipo in ("tutti", "gas") and entity_gas:
        qs_gas = BollettaGas.objects.all().order_by("-periodo_fine")
        if bolletta_id:
            qs_gas = qs_gas.filter(pk=bolletta_id)

        for g in qs_gas:
            dt_inizio = timezone.make_aware(datetime.combine(g.periodo_inizio, datetime.min.time()), tz)
            dt_fine = timezone.make_aware(datetime.combine(g.periodo_fine, datetime.max.time()), tz)

            try:
                lettura = ha_client.get_energia_consumata(entity_gas, dt_inizio, dt_fine)
            except Exception as exc:
                logger.warning("Errore polling HA gas per %s: %s", g, exc)
                lettura = None

            if lettura and lettura.get("delta") is not None and lettura["delta"] > 0:
                smc_ha = Decimal(str(round(lettura["delta"], 2)))
                smc_fatt = Decimal(str(g.smc_fatturati or 0))

                scost_pct = None
                if smc_ha > 0:
                    scost_pct = ((smc_fatt - smc_ha) / smc_ha * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                g.smc_misurati_ha = smc_ha
                g.scostamento_ha_pct = scost_pct
                g.fonte_misura_ha = f"Polling REST ({entity_gas})"
                g.save(update_fields=["smc_misurati_ha", "scostamento_ha_pct", "fonte_misura_ha"])

                aggiornati += 1
                dettagli.append({
                    "id": g.pk,
                    "tipo": "gas",
                    "bolletta": str(g),
                    "fatturato": f"{smc_fatt} Smc",
                    "misurato_ha": f"{smc_ha} Smc",
                    "scostamento_pct": float(scost_pct) if scost_pct else 0.0,
                    "stato": "congruo" if abs(scost_pct or 0) <= 3 else ("moderato" if abs(scost_pct or 0) <= 8 else "anomalo"),
                })

    return {
        "ok": True,
        "aggiornati": aggiornati,
        "dettagli": dettagli,
        "messaggio": f"Polling completato con successo su {aggiornati} bollette." if aggiornati > 0 else "Nessuna misura trovata per i periodi selezionati.",
    }


# ===========================================================================
# 2. CARICAMENTO MANUALE VIA CSV EXPORT HOME ASSISTANT
# ===========================================================================
def _parse_data_csv(testo_data: str) -> date | None:
    """Riconosce diversi formati di data usati negli export di Home Assistant."""
    testo = testo_data.strip().split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(testo, fmt).date()
        except ValueError:
            pass
    return None


def elabora_csv_home_assistant(file_obj, tipo_predefinito: str = "luce") -> dict[str, Any]:
    """
    Analizza un file CSV esportato da Home Assistant (Energy Dashboard o Statistics),
    estrae le letture giornaliere/periodiche e aggiorna le bollette corrispondenti.
    """
    contenuto = file_obj.read()
    if isinstance(contenuto, bytes):
        try:
            testo = contenuto.decode("utf-8-sig")
        except UnicodeDecodeError:
            testo = contenuto.decode("latin-1")
    else:
        testo = contenuto

    righe = testo.splitlines()
    if not righe:
        return {"ok": False, "errore": "Il file CSV caricato è vuoto."}

    # Riconosce delimitatore (virgola o punto e virgola)
    prima_riga = righe[0]
    delimitatore = ";" if ";" in prima_riga else ","

    reader = csv.reader(righe, delimiter=delimitatore)
    intestazione = [c.strip().lower() for c in next(reader, [])]

    # Identifica colonna Data e colonna Valore
    col_data_idx = -1
    col_val_idx = -1

    for idx, col in enumerate(intestazione):
        if any(term in col for term in ("start", "date", "data", "timestamp", "periodo", "giorno", "time")):
            if col_data_idx == -1:
                col_data_idx = idx
        if any(term in col for term in ("sum", "state", "kwh", "smc", "consumo", "value", "grid", "energia")):
            if col_val_idx == -1:
                col_val_idx = idx

    if col_data_idx == -1 or col_val_idx == -1:
        # Fallback posizionale standard (Colonna 0: Data, Colonna 1: Consumo)
        col_data_idx = 0
        col_val_idx = 1 if len(intestazione) > 1 else 0

    # Raccogli le misure giornaliere dal CSV: {date: valore}
    misure_giornaliere: list[tuple[date, float]] = []
    for riga in reader:
        if len(riga) <= max(col_data_idx, col_val_idx):
            continue
        str_data = riga[col_data_idx]
        str_val = riga[col_val_idx].replace(",", ".").replace("€", "").strip()

        dt = _parse_data_csv(str_data)
        if not dt:
            continue

        try:
            val = float(str_val)
            misure_giornaliere.append((dt, val))
        except ValueError:
            continue

    if not misure_giornaliere:
        return {"ok": False, "errore": "Nessuna riga valida con data e valore numerico trovata nel CSV."}

    # Se le misure sono cumulative (es. `sum` sempre crescente), calcola i delta
    # Se sono già consumi per periodo (es. kWh/giorno), somma semplicemente
    is_cumulativo = False
    if len(misure_giornaliere) >= 3:
        if misure_giornaliere[0][1] < misure_giornaliere[1][1] < misure_giornaliere[2][1] and misure_giornaliere[-1][1] > 1000:
            is_cumulativo = True

    # Abbina alle bollette
    aggiornate = 0
    bollette_luce = list(BollettaElettrica.objects.all())
    bollette_gas = list(BollettaGas.objects.all())

    # Determina se il CSV è gas (se contiene Smc) o luce (kWh)
    is_gas = "smc" in prima_riga.lower() or "gas" in prima_riga.lower() or tipo_predefinito == "gas"

    if not is_gas:
        for b in bollette_luce:
            p_ini, p_fin = b.periodo_inizio, b.periodo_fine
            punti = [v for d, v in misure_giornaliere if p_ini <= d <= p_fin]
            if not punti:
                continue

            if is_cumulativo and len(punti) >= 2:
                consumo_ha = Decimal(str(round(punti[-1] - punti[0], 2)))
            else:
                consumo_ha = Decimal(str(round(sum(punti), 2)))

            if consumo_ha > 0:
                kwh_fatt = Decimal(str(b.kwh_fatturati or 0))
                scost_pct = ((kwh_fatt - consumo_ha) / consumo_ha * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                b.kwh_misurati_ha = consumo_ha
                b.scostamento_ha_pct = scost_pct
                b.fonte_misura_ha = "Import CSV Home Assistant"
                b.save(update_fields=["kwh_misurati_ha", "scostamento_ha_pct", "fonte_misura_ha"])
                aggiornate += 1
    else:
        for g in bollette_gas:
            p_ini, p_fin = g.periodo_inizio, g.periodo_fine
            punti = [v for d, v in misure_giornaliere if p_ini <= d <= p_fin]
            if not punti:
                continue

            if is_cumulativo and len(punti) >= 2:
                consumo_ha = Decimal(str(round(punti[-1] - punti[0], 2)))
            else:
                consumo_ha = Decimal(str(round(sum(punti), 2)))

            if consumo_ha > 0:
                smc_fatt = Decimal(str(g.smc_fatturati or 0))
                scost_pct = ((smc_fatt - consumo_ha) / consumo_ha * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                g.smc_misurati_ha = consumo_ha
                g.scostamento_ha_pct = scost_pct
                g.fonte_misura_ha = "Import CSV Home Assistant"
                g.save(update_fields=["smc_misurati_ha", "scostamento_ha_pct", "fonte_misura_ha"])
                aggiornate += 1

    return {
        "ok": True,
        "righe_lette": len(misure_giornaliere),
        "aggiornate": aggiornate,
        "is_cumulativo": is_cumulativo,
        "is_gas": is_gas,
        "messaggio": f"Importate {len(misure_giornaliere)} righe. Aggiornate {aggiornate} bollette corrispondenti.",
    }


# ===========================================================================
# 3. RIEPILOGO & STATISTICHE GLOBALI DI CONFRONTO
# ===========================================================================
def ottieni_riepilogo_confronto() -> dict[str, Any]:
    """
    Raccoglie tutte le bollette con confronto HA attivo e calcola i KPI globali:
    - Scostamento medio (%)
    - Totale discrepanza fisica (kWh e Smc)
    - Impatto economico stimato (€)
    - Grado di accuratezza contatore
    """
    bollette_luce = list(BollettaElettrica.objects.all().order_by("-periodo_fine"))
    bollette_gas = list(BollettaGas.objects.all().order_by("-periodo_fine"))

    confronti_luce = []
    tot_fatt_kwh = Decimal("0")
    tot_ha_kwh = Decimal("0")
    impatto_eur_luce = Decimal("0")

    for b in bollette_luce:
        kwh_f = Decimal(str(b.kwh_fatturati or 0))
        kwh_ha = b.kwh_misurati_ha
        scost_pct = b.scostamento_ha_pct

        delta_kwh = None
        impatto_eur = None
        stato = "non_rilevato"

        if kwh_ha is not None and kwh_ha > 0:
            delta_kwh = (kwh_f - kwh_ha).quantize(Decimal("0.01"))
            prezzo_marg = Decimal(str(b.prezzo_marginale_medio or 0.20))
            impatto_eur = (delta_kwh * prezzo_marg).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            
            tot_fatt_kwh += kwh_f
            tot_ha_kwh += kwh_ha
            impatto_eur_luce += impatto_eur

            abs_scost = abs(float(scost_pct or 0))
            if abs_scost <= 3.0:
                stato = "congruo"
            elif abs_scost <= 8.0:
                stato = "moderato"
            else:
                stato = "anomalo"

        confronti_luce.append({
            "bolletta": b,
            "tipo": "luce",
            "fatturato": kwh_f,
            "misurato_ha": kwh_ha,
            "delta": delta_kwh,
            "scostamento_pct": scost_pct,
            "impatto_eur": impatto_eur,
            "stato": stato,
            "fonte": b.fonte_misura_ha or "—",
        })

    confronti_gas = []
    tot_fatt_smc = Decimal("0")
    tot_ha_smc = Decimal("0")
    impatto_eur_gas = Decimal("0")

    for g in bollette_gas:
        smc_f = Decimal(str(g.smc_fatturati or 0))
        smc_ha = g.smc_misurati_ha
        scost_pct = g.scostamento_ha_pct

        delta_smc = None
        impatto_eur = None
        stato = "non_rilevato"

        if smc_ha is not None and smc_ha > 0:
            delta_smc = (smc_f - smc_ha).quantize(Decimal("0.01"))
            prezzo_marg = Decimal(str(g.prezzo_marginale_medio_smc or 0.65))
            impatto_eur = (delta_smc * prezzo_marg).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            tot_fatt_smc += smc_f
            tot_ha_smc += smc_ha
            impatto_eur_gas += impatto_eur

            abs_scost = abs(float(scost_pct or 0))
            if abs_scost <= 3.0:
                stato = "congruo"
            elif abs_scost <= 8.0:
                stato = "moderato"
            else:
                stato = "anomalo"

        confronti_gas.append({
            "bolletta": g,
            "tipo": "gas",
            "fatturato": smc_f,
            "misurato_ha": smc_ha,
            "delta": delta_smc,
            "scostamento_pct": scost_pct,
            "impatto_eur": impatto_eur,
            "stato": stato,
            "fonte": g.fonte_misura_ha or "—",
        })

    # Calcolo medie e accuratezza
    n_validati_luce = sum(1 for c in confronti_luce if c["misurato_ha"] is not None)
    n_validati_gas = sum(1 for c in confronti_gas if c["misurato_ha"] is not None)

    delta_tot_kwh = (tot_fatt_kwh - tot_ha_kwh) if n_validati_luce > 0 else Decimal("0")
    delta_tot_smc = (tot_fatt_smc - tot_ha_smc) if n_validati_gas > 0 else Decimal("0")

    accuratezza_pct = None
    if tot_ha_kwh > 0:
        accuratezza_pct = round(100 - abs(float((delta_tot_kwh / tot_ha_kwh) * 100)), 1)
        accuratezza_pct = max(0.0, min(100.0, accuratezza_pct))

    return {
        "confronti_luce": confronti_luce,
        "confronti_gas": confronti_gas,
        "n_validati_luce": n_validati_luce,
        "n_validati_gas": n_validati_gas,
        "tot_validati": n_validati_luce + n_validati_gas,
        "tot_fatt_kwh": tot_fatt_kwh,
        "tot_ha_kwh": tot_ha_kwh,
        "delta_tot_kwh": delta_tot_kwh,
        "impatto_eur_luce": impatto_eur_luce,
        "tot_fatt_smc": tot_fatt_smc,
        "tot_ha_smc": tot_ha_smc,
        "delta_tot_smc": delta_tot_smc,
        "impatto_eur_gas": impatto_eur_gas,
        "impatto_eur_complessivo": impatto_eur_luce + impatto_eur_gas,
        "accuratezza_pct": accuratezza_pct,
        "ha_configurato": ha_client.is_configured(),
    }

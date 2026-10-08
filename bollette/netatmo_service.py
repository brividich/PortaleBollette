"""
Servizio di elaborazione e sincronizzazione dei file di utilizzo Netatmo Smart Thermostat.

Funzionalità:
1. Parsing intelligente ed euristico di file CSV (e formati tabellari) esportati da Netatmo Energy WebApp:
   - Riconoscimento automatico encoding (UTF-8, UTF-8-BOM, Latin-1, Windows-1252)
   - Riconoscimento delimitatori (virgola, punto e virgola, tab) e formato decimali (. o ,)
   - Supporto formati temporali (Unix timestamp, date ISO, date europee GG/MM/AAAA con o senza ore)
   - Supporto colonne: data/ora, BoilerOn (secondi, minuti o stato fiamma), Temperature, Sp-Temperature
2. Aggregazione giornaliera in NetatmoRecordGiornaliero
3. Correlazione automatica con le bollette gas (BollettaGas):
   - Calcolo ore totali accensione caldaia per il periodo esatto di fatturazione
   - Consumo orario effettivo del bruciatore: Smc / ora caldaia
   - Costo orario del riscaldamento: € / ora caldaia
   - Temperatura interna media dell'abitazione
   - Scomposizione analitica del consumo: Riscaldamento ambiente vs Acqua Calda Sanitaria (ACS) / Cottura
4. Diagnostica e statistiche per la dashboard
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db.models import Avg, Sum
from django.utils import timezone

from .models import BollettaGas, NetatmoRecordGiornaliero

logger = logging.getLogger("bollette.netatmo")


def _decodifica_file(file_obj) -> str:
    """Legge il contenuto binario del file provando diversi encoding comuni."""
    raw = file_obj.read()
    if isinstance(raw, str):
        return raw

    encodings = ["utf-8-sig", "utf-8", "latin-1", "cp1252", "iso-8859-1"]
    for enc in encodings:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _rileva_delimitatore(testo: str) -> str:
    """Determina se il delimitatore principale è virgola, punto e virgola o tabulazione."""
    prime_righe = "\n".join(testo.splitlines()[:10])
    try:
        sniffer = csv.Sniffer()
        dialetto = sniffer.sniff(prime_righe, delimiters=",;\t")
        return dialetto.delimiter
    except Exception:
        # Conteggio fallback
        c_virgole = prime_righe.count(",")
        c_punto_virgole = prime_righe.count(";")
        c_tab = prime_righe.count("\t")
        if c_punto_virgole > c_virgole and c_punto_virgole > c_tab:
            return ";"
        if c_tab > c_virgole and c_tab > c_punto_virgole:
            return "\t"
        return ","


def _parse_data_ora(valore: str) -> datetime | date | None:
    """Interpreta una stringa di data/ora o un timestamp numerico."""
    if not valore:
        return None
    valore = valore.strip()

    # Tentativo 1: Timestamp Unix (secondi o millisecondi)
    try:
        val_float = float(valore)
        if val_float > 10000000000:  # millisecondi
            val_float = val_float / 1000.0
        if 946684800 < val_float < 2524608000:  # tra il 2000 e il 2050
            return datetime.fromtimestamp(val_float)
    except ValueError:
        pass

    # Tentativo 2: Formati standard
    formati = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
    ]
    for fmt in formati:
        try:
            return datetime.strptime(valore, fmt)
        except ValueError:
            continue

    return None


def _parse_numero(valore: Any) -> float | None:
    """Converte un valore in float gestendo sia il punto che la virgola decimale."""
    if valore is None:
        return None
    if isinstance(valore, (int, float)):
        return float(valore)

    v_str = str(valore).strip().replace(" ", "").replace("%", "")
    if not v_str or v_str in ("-", "null", "none", "nan"):
        return None

    # Se contiene virgola e punto, es. "1.234,56" o "1,234.56"
    if "," in v_str and "." in v_str:
        if v_str.rfind(",") > v_str.rfind("."):
            v_str = v_str.replace(".", "").replace(",", ".")
        else:
            v_str = v_str.replace(",", "")
    else:
        v_str = v_str.replace(",", ".")

    try:
        return float(v_str)
    except ValueError:
        return None


def elabora_csv_netatmo(file_obj, nome_file: str = "") -> dict[str, Any]:
    """
    Esegue il parsing di un file esportato da Netatmo Energy WebApp e salva i dati giornalieri.
    Ritorna un dizionario di report con statistiche sul numero di righe e giorni importati.
    """
    testo = _decodifica_file(file_obj)
    righe = [r for r in testo.splitlines() if r.strip()]

    if not righe:
        return {"ok": False, "errore": "Il file fornito è vuoto o non leggibile."}

    delimitatore = _rileva_delimitatore(testo)
    reader = csv.reader(io.StringIO(testo), delimiter=delimitatore)

    # Identificazione dell'intestazione (header)
    colonne_trovate: list[str] = []
    riga_intestazione_idx = 0

    for idx, r in enumerate(reader):
        colonne_normalizzate = [c.strip().lower() for c in r]
        # Cerchiamo colonne chiave tipiche di Netatmo
        ha_tempo = any(k in c for c in colonne_normalizzate for k in ("time", "date", "timestamp", "giorno", "data"))
        ha_metrica = any(k in c for c in colonne_normalizzate for k in ("boiler", "temp", "caldaia", "heat", "setpoint", "sp-"))
        if ha_tempo and ha_metrica:
            colonne_trovate = colonne_normalizzate
            riga_intestazione_idx = idx
            break

    if not colonne_trovate:
        # Fallback alla prima riga
        r_prima = righe[0].split(delimitatore)
        colonne_trovate = [c.strip().lower() for c in r_prima]

    # Mappatura indici colonne
    idx_data = -1
    idx_boiler = -1
    idx_temp = -1
    idx_setpoint = -1

    for i, col in enumerate(colonne_trovate):
        # Data / ora
        if idx_data == -1 and any(k in col for k in ("timestamp", "timezone", "datetime", "date", "data", "time", "giorno")):
            idx_data = i
        # Caldaia / Heating
        if idx_boiler == -1 and any(k in col for k in ("boileron", "boiler_on", "heatingdemand", "heating_demand", "heating_time", "heat_on", "boiler", "caldaia")):
            idx_boiler = i
        # Temperatura ambiente
        if idx_temp == -1 and any(k in col for k in ("temperature", "temp", "temperatura", "t_interna", "ambient_temp")) and not any(k in col for k in ("sp-", "setpoint", "sp_")):
            idx_temp = i
        # Setpoint
        if idx_setpoint == -1 and any(k in col for k in ("sp-temperature", "sp_temperature", "setpoint", "sp-temp", "target_temp", "t_setpoint")):
            idx_setpoint = i

    if idx_data == -1:
        return {
            "ok": False,
            "errore": f"Impossibile identificare la colonna della Data o del Timestamp nel CSV. Colonne trovate: {', '.join(colonne_trovate)}",
        }

    # Struttura per aggregazione giornaliera: {data: {"secondi": 0, "temps": [], "setpoints": [], "count": 0}}
    giorni_data: dict[date, dict[str, Any]] = {}
    tot_righe_lette = 0
    righe_scartate = 0

    # Riapre il reader saltando le righe fino all'header
    reader = csv.reader(io.StringIO(testo), delimiter=delimitatore)
    for _ in range(riga_intestazione_idx + 1):
        try:
            next(reader)
        except StopIteration:
            break

    for riga in reader:
        if not riga or len(riga) <= idx_data:
            continue

        tot_righe_lette += 1
        dt_val = _parse_data_ora(riga[idx_data])
        if not dt_val:
            righe_scartate += 1
            continue

        giorno = dt_val.date() if isinstance(dt_val, datetime) else dt_val

        if giorno not in giorni_data:
            giorni_data[giorno] = {
                "secondi": 0.0,
                "temps": [],
                "setpoints": [],
                "count": 0,
            }

        # BoilerOn
        secondi_caldaia = 0.0
        if idx_boiler != -1 and idx_boiler < len(riga):
            val_boiler = _parse_numero(riga[idx_boiler])
            if val_boiler is not None and val_boiler > 0:
                # Netatmo WebApp esporta solitamente i secondi di accensione nel periodo (es. 1800 s per 30m)
                # Se i valori sono <= 1.0 (on/off booleano per intervallo orario), stimiamo 3600s
                if val_boiler == 1.0 and "state" in colonne_trovate[idx_boiler]:
                    secondi_caldaia = 3600.0
                else:
                    secondi_caldaia = val_boiler

        giorni_data[giorno]["secondi"] += secondi_caldaia
        giorni_data[giorno]["count"] += 1

        # Temperature
        if idx_temp != -1 and idx_temp < len(riga):
            t_val = _parse_numero(riga[idx_temp])
            if t_val is not None and -10.0 <= t_val <= 45.0:
                giorni_data[giorno]["temps"].append(t_val)

        # Setpoint
        if idx_setpoint != -1 and idx_setpoint < len(riga):
            sp_val = _parse_numero(riga[idx_setpoint])
            if sp_val is not None and 5.0 <= sp_val <= 35.0:
                giorni_data[giorno]["setpoints"].append(sp_val)

    if not giorni_data:
        return {
            "ok": False,
            "errore": f"Nessun dato temporale valido estratto su {tot_righe_lette} righe analizzate.",
        }

    # Salvataggio nel database dei record giornalieri
    creati = 0
    aggiornati = 0

    for giorno, dati in giorni_data.items():
        secondi_tot = int(round(dati["secondi"]))
        # Protezione: massimo 86400 secondi in un giorno (24 ore)
        secondi_tot = min(86400, secondi_tot)

        t_media = (
            Decimal(str(round(sum(dati["temps"]) / len(dati["temps"]), 2)))
            if dati["temps"]
            else None
        )
        sp_media = (
            Decimal(str(round(sum(dati["setpoints"]) / len(dati["setpoints"]), 2)))
            if dati["setpoints"]
            else None
        )

        record, created = NetatmoRecordGiornaliero.objects.update_or_create(
            data=giorno,
            defaults={
                "secondi_caldaia": secondi_tot,
                "temp_interna_media": t_media,
                "temp_setpoint_media": sp_media,
                "n_campionamenti": dati["count"],
                "fonte_file": nome_file or "Import CSV Netatmo",
            },
        )
        if created:
            creati += 1
        else:
            aggiornati += 1

    # Sincronizza automaticamente le bollette del gas archiviate
    bollette_aggiornate = sincronizza_bollette_gas_con_netatmo()

    return {
        "ok": True,
        "tot_righe_lette": tot_righe_lette,
        "righe_scartate": righe_scartate,
        "giorni_elaborati": len(giorni_data),
        "creati": creati,
        "aggiornati": aggiornati,
        "bollette_gas_aggiornate": bollette_aggiornate,
        "prima_data": min(giorni_data.keys()).strftime("%d/%m/%Y"),
        "ultima_data": max(giorni_data.keys()).strftime("%d/%m/%Y"),
    }


def sincronizza_bollette_gas_con_netatmo(bolletta_id: int | None = None) -> int:
    """
    Ricalcola e aggiorna le metriche Netatmo per le bollette del gas archiviate.
    Se bolletta_id è fornito, aggiorna solo quella singola bolletta.
    Ritorna il numero di bollette aggiornate.
    """
    qs = BollettaGas.objects.all().order_by("periodo_fine")
    if bolletta_id:
        qs = qs.filter(pk=bolletta_id)

    aggiornate = 0

    # Calcolo del baseload estivo del gas (consumo per ACS e cucina a riscaldamento spento)
    # Se abbiamo bollette con zero ore di caldaia o nei mesi estivi (giugno-settembre), usiamo la loro media
    baseload_acs_die = _stima_baseload_acs_giornaliero()

    for b in qs:
        records = NetatmoRecordGiornaliero.objects.filter(
            data__gte=b.periodo_inizio,
            data__lte=b.periodo_fine,
        )

        if not records.exists():
            continue

        tot_secondi = sum(r.secondi_caldaia for r in records)
        ore_caldaia = round(float(tot_secondi) / 3600.0, 2)

        # Temperatura interna media ponderata
        temps = [float(r.temp_interna_media) for r in records if r.temp_interna_media is not None]
        temp_media_int = (
            Decimal(str(round(sum(temps) / len(temps), 2))) if temps else None
        )

        smc_tot = float(b.smc_fatturati or 0)
        importo_tot = float(b.importo_totale or 0)

        # 1. Consumo orario bruciatore (Smc / ora di caldaia)
        smc_ora = None
        costo_ora = None
        if ore_caldaia > 0 and smc_tot > 0:
            smc_ora = Decimal(str(round(smc_tot / ore_caldaia, 4)))
            costo_ora = Decimal(str(round(importo_tot / ore_caldaia, 2))) if importo_tot > 0 else None

        # 2. Scomposizione ACS/Cucina vs Riscaldamento
        giorni = b.giorni_periodo
        smc_acs_stimati = min(
            Decimal(str(smc_tot)),
            Decimal(str(round(baseload_acs_die * giorni, 2)))
        )
        smc_riscaldamento_stimati = max(
            Decimal("0"),
            Decimal(str(round(smc_tot - float(smc_acs_stimati), 2)))
        )

        b.ore_caldaia = Decimal(str(ore_caldaia))
        b.temp_interna_media = temp_media_int
        b.smc_ora_caldaia = smc_ora
        b.costo_ora_caldaia = costo_ora
        b.smc_riscaldamento_stimati = smc_riscaldamento_stimati
        b.smc_acs_cucina_stimati = smc_acs_stimati
        b.save(update_fields=[
            "ore_caldaia", "temp_interna_media", "smc_ora_caldaia",
            "costo_ora_caldaia", "smc_riscaldamento_stimati", "smc_acs_cucina_stimati",
        ])
        aggiornate += 1

    return aggiornate


def _stima_baseload_acs_giornaliero() -> float:
    """
    Stima il consumo giornaliero fisso di gas per Acqua Calda Sanitaria e cottura (Smc/die).
    Cerca nei mesi estivi (luglio, agosto) o usa un default realistico (0.35 Smc/giorno).
    """
    bollette_estive = BollettaGas.objects.filter(
        periodo_fine__month__in=[6, 7, 8, 9],
        smc_fatturati__gt=0,
    )
    if bollette_estive.exists():
        medie = [b.smc_giorno for b in bollette_estive if b.smc_giorno > 0]
        if medie:
            return round(sum(medie) / len(medie), 2)

    return 0.35  # default medio per famiglia italiana (cottura + docce)


def ottieni_statistiche_netatmo() -> dict[str, Any]:
    """
    Raccoglie statistiche e serie storiche per la dashboard Netatmo & Riscaldamento.
    Ritorna metriche aggregate, dati per Chart.js e aggregati per mese.
    """
    records = NetatmoRecordGiornaliero.objects.all().order_by("data")
    tot_giorni = records.count()

    if tot_giorni == 0:
        return {
            "disponibile": False,
            "tot_giorni": 0,
            "ore_caldaia_totali": 0.0,
            "temp_interna_media": None,
            "temp_setpoint_media": None,
            "chart_json": "{}",
            "mesi": [],
        }

    tot_secondi = records.aggregate(s=Sum("secondi_caldaia"))["s"] or 0
    ore_totali = round(float(tot_secondi) / 3600.0, 1)

    t_int_avg = records.filter(temp_interna_media__isnull=False).aggregate(t=Avg("temp_interna_media"))["t"]
    t_sp_avg = records.filter(temp_setpoint_media__isnull=False).aggregate(t=Avg("temp_setpoint_media"))["t"]

    # Giorni con riscaldamento attivo (> 0 ore)
    giorni_accesi = records.filter(secondi_caldaia__gt=0).count()
    media_ore_giorno_attivo = round(ore_totali / giorni_accesi, 1) if giorni_accesi > 0 else 0.0

    # Raggruppamento per mese
    mesi_dict: dict[str, dict[str, Any]] = {}
    for r in records:
        chiave = r.data.strftime("%Y-%m")
        if chiave not in mesi_dict:
            mesi_dict[chiave] = {
                "chiave": chiave,
                "label": r.data.strftime("%B %Y").capitalize(),
                "anno": r.data.year,
                "mese": r.data.month,
                "secondi": 0,
                "temps": [],
                "giorni": 0,
                "giorni_accesi": 0,
            }
        mesi_dict[chiave]["secondi"] += r.secondi_caldaia
        mesi_dict[chiave]["giorni"] += 1
        if r.secondi_caldaia > 0:
            mesi_dict[chiave]["giorni_accesi"] += 1
        if r.temp_interna_media is not None:
            mesi_dict[chiave]["temps"].append(float(r.temp_interna_media))

    elenco_mesi = []
    for k, m in sorted(mesi_dict.items(), reverse=True):
        ore_m = round(float(m["secondi"]) / 3600.0, 1)
        t_m = round(sum(m["temps"]) / len(m["temps"]), 1) if m["temps"] else None

        # Cerca se c'è una bolletta gas corrispondente al mese
        b_gas = BollettaGas.objects.filter(
            periodo_fine__year=m["anno"],
            periodo_fine__month=m["mese"],
        ).first()

        elenco_mesi.append({
            "label": m["label"],
            "anno": m["anno"],
            "mese": m["mese"],
            "giorni": m["giorni"],
            "giorni_accesi": m["giorni_accesi"],
            "ore_caldaia": ore_m,
            "temp_media": t_m,
            "smc_bolletta": b_gas.smc_fatturati if b_gas else None,
            "spesa_bolletta": b_gas.importo_totale if b_gas else None,
            "smc_ora": b_gas.smc_ora_caldaia if b_gas else (round(float(b_gas.smc_fatturati) / ore_m, 3) if b_gas and ore_m > 0 and b_gas.smc_fatturati else None),
            "bolletta_id": b_gas.pk if b_gas else None,
        })

    # Dati per grafico Chart.js (serie giornaliera)
    # Limita agli ultimi 120 giorni per leggibilità o tutti se pochi
    cronologia = list(records.order_by("data"))[-120:]
    chart_data = {
        "labels": [r.data.strftime("%d/%m") for r in cronologia],
        "ore_caldaia": [r.ore_caldaia for r in cronologia],
        "temp_interna": [float(r.temp_interna_media) if r.temp_interna_media is not None else None for r in cronologia],
        "temp_setpoint": [float(r.temp_setpoint_media) if r.temp_setpoint_media is not None else None for r in cronologia],
    }

    import json
    return {
        "disponibile": True,
        "tot_giorni": tot_giorni,
        "prima_data": records.first().data,
        "ultima_data": records.last().data,
        "ore_caldaia_totali": ore_totali,
        "giorni_accesi": giorni_accesi,
        "media_ore_giorno_attivo": media_ore_giorno_attivo,
        "temp_interna_media": round(float(t_int_avg), 1) if t_int_avg is not None else None,
        "temp_setpoint_media": round(float(t_sp_avg), 1) if t_sp_avg is not None else None,
        "chart_json": json.dumps(chart_data),
        "mesi": elenco_mesi,
    }

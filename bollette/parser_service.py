"""
Servizio avanzato e robusto di Ingestion e Parsing di Bollette Energetiche Italiane (PDF o testo).

Caratteristiche:
1. Estrazione testo multi-pagina con pulizia, de-iphenation, rimozione null bytes e normalizzazione spaziale.
2. Rilevamento automatico del tipo di fornitura (Luce vs Gas) con scoring contestuale e gestione dual fuel.
3. Catalogo esteso degli operatori italiani (oltre 35 fornitori di mercato libero e maggior tutela).
4. Estrazione deterministica POD (Luce, 14-15 char) e PDR (Gas, 14 cifre) con tolleranza spazi/trattini.
5. Parser date italiano completo (formati numerici, testuali, mesi estesi e abbreviati, bimestri e mesi di competenza).
6. Estrazione importi con gestione note di credito, separatori decimali e migliaia italiani.
7. Estrazione consumi (kWh / Smc) e disaggregazione per fasce orarie (F1, F2, F3) con somma euristica.
8. Estrazione parametri tecnici gas: Coefficiente di conversione C e Potere Calorifico Superiore (PCS).
9. Estrazione breakdown economico delle componenti ARERA (Spesa materia, Trasporto, Oneri di sistema, Imposte, Canone RAI).
10. Scoring di affidabilità (Confidence Score 0-100%), tracciamento campi estratti vs mancanti e avvisi diagnostici.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError

logger = logging.getLogger("bollette.parser")

# ===========================================================================
# COSTANTI E MAPPATURE
# ===========================================================================
MESI_ITALIANI: dict[str, int] = {
    "gennaio": 1, "gen": 1, "genn": 1,
    "febbraio": 2, "feb": 2, "febbr": 2,
    "marzo": 3, "mar": 3,
    "aprile": 4, "apr": 4,
    "maggio": 5, "mag": 5,
    "giugno": 6, "giu": 6,
    "luglio": 7, "lug": 7,
    "agosto": 8, "ago": 8,
    "settembre": 9, "set": 9, "sett": 9,
    "ottobre": 10, "ott": 10,
    "novembre": 11, "nov": 11,
    "dicembre": 12, "dic": 12,
}

GIORNI_PER_MESE: dict[int, int] = {
    1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
    7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
}

CATALOGO_FORNITORI: list[tuple[str, list[str]]] = [
    ("Enel Energia", ["enel energia", "enel s.p.a", "enel spa", "enel.it"]),
    ("Servizio Elettrico Nazionale", [
        "servizio elettrico nazionale", "servizioelettriconazionale",
        "maggior tutela", "servizio di maggior tutela"
    ]),
    ("Eni Plenitude", [
        "eni plenitude", "plenitude", "eni gas e luce",
        "eni gas & luce", "eni s.p.a.", "eni spa"
    ]),
    ("Acea Energia", ["acea energia", "acea spa", "acea.it", "gruppo acea"]),
    ("A2A Energia", ["a2a energia", "a2a spa", "gruppo a2a", "a2a.eu"]),
    ("Octopus Energy", ["octopus energy", "octopus", "octopusenergy.it"]),
    ("Edison Energia", ["edison energia", "edison next", "edison spa", "edison.it"]),
    ("Hera Comm", ["hera comm", "gruppo hera", "hera spa", "heracomm.it"]),
    ("Iren Mercato", ["iren mercato", "iren luce gas", "gruppo iren", "iren spa", "irenlucegas.it"]),
    ("Sorgenia", ["sorgenia spa", "sorgenia", "sorgenia.it"]),
    ("NeN", ["nen energia", "nen", "nen mercato", "nen.it"]),
    ("Dolomiti Energia", ["dolomiti energia", "dolomiti", "dolomitienergia.it"]),
    ("E.ON", ["e.on energia", "e.on", "eon energia", "eon", "eon.it"]),
    ("Engie", ["engie italia", "engie", "engie.it"]),
    ("Poste Energia", ["poste energia", "poste italiane", "poste.it"]),
    ("Illumia", ["illumia spa", "illumia", "illumia.it"]),
    ("Alperia", ["alperia smart", "alperia energy", "alperia", "alperia.eu"]),
    ("Estra", ["estra energie", "estra", "gruppo estra", "estraspa.it"]),
    ("Wekiwi", ["wekiwi", "wekiwi.it"]),
    ("Pulsee", ["pulsee luce e gas", "pulsee", "axpo italia", "axpo", "pulsee.it"]),
    ("Servizio Elettrico Roma", ["servizio elettrico roma"]),
    ("AGSM AIM", ["agsm aim", "agsm", "aim vicenza"]),
    ("Nuovenergie", ["nuovenergie", "nuovenergie spa"]),
    ("Gesam Gas & Luce", ["gesam gas", "gesam"]),
    ("Enegan", ["enegan luce", "enegan"]),
    ("Tate", ["tate", "tate.it"]),
    ("Spigas Clienti", ["spigas clienti", "spigas"]),
    ("Optima Italia", ["optima italia", "optima"]),
    ("Metamer", ["metamer"]),
    ("Sinergas", ["sinergas"]),
    ("Ascopiave", ["ascopiave"]),
    ("Green Network", ["green network"]),
    ("Coop Voce & Luce", ["coop luce", "accendi luce & gas", "accendi luce"]),
]


# ===========================================================================
# PULIZIA ED ESTRAZIONE TESTO DA PDF
# ===========================================================================
def normalizza_testo(raw_text: str) -> str:
    """Pulisce caratteri nulli, spazi speciali e normalizza la punteggiatura."""
    if not raw_text:
        return ""
    # Rimuovi null byte e caratteri non stampabili (conserva newline e tab)
    t = raw_text.replace("\x00", " ")
    # Normalizza spazi non-breaking
    t = t.replace("\xa0", " ").replace("\u202f", " ").replace("\u3000", " ")
    # Sostituisci legature tipiche
    t = t.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("’", "'").replace("–", "-").replace("—", "-")
    # Normalizza sequenze di spazi mantenendo i newline
    t = re.sub(r"[ \t]+", " ", t)
    # Correggi casi noti di lettere distanziate tipo "P O D" o "P D R" o "k W h"
    t = re.sub(r"\bP\s*O\s*D\b", "POD", t, flags=re.IGNORECASE)
    t = re.sub(r"\bP\s*D\s*R\b", "PDR", t, flags=re.IGNORECASE)
    t = re.sub(r"\bk\s*W\s*h\b", "kWh", t, flags=re.IGNORECASE)
    t = re.sub(r"\bS\s*m\s*c\b", "Smc", t, flags=re.IGNORECASE)
    return t


def estrai_testo_pdf(file_obj) -> tuple[str, list[str], str | None]:
    """
    Estrae il testo da tutte le pagine del file PDF.
    Ritorna:
        (testo_completo, lista_pagine, eventuale_errore)
    """
    try:
        # Se è un file aperto (InMemoryUploadedFile o simile), riavvolgi
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)

        reader = PdfReader(file_obj)

        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return "", [], "Il file PDF è protetto da password e non può essere letto automaticamente."

        pagine_testo: list[str] = []
        for i, pagina in enumerate(reader.pages):
            try:
                txt = pagina.extract_text() or ""
                pagine_testo.append(normalizza_testo(txt))
            except Exception as p_err:
                logger.warning("Errore estrazione pagina %d: %s", i + 1, p_err)
                pagine_testo.append("")

        testo_completo = "\n--- PAGINA ---\n".join(pagine_testo)
        if not testo_completo.strip() or len(testo_completo.strip()) < 20:
            return testo_completo, pagine_testo, (
                "Il PDF non contiene testo vettoriale selezionabile (probabile scansione o immagine pura). "
                "Il file può comunque essere archiviato compilando i campi manualmente."
            )

        return testo_completo, pagine_testo, None

    except FileNotDecryptedError:
        return "", [], "PDF protetto da password."
    except PdfReadError as pre:
        return "", [], f"File PDF danneggiato o non valido: {pre}"
    except Exception as exc:
        logger.error("Errore generico lettura PDF: %s", exc)
        return "", [], f"Impossibile leggere il file PDF: {exc}"


# ===========================================================================
# PARSER HELPER: NUMERI E DATE
# ===========================================================================
def _parse_numero(stringa: str | None) -> Decimal | None:
    """
    Converte un numero in formato valuta o quantitativo italiano in Decimal.
    Esempi:
        '1.250,50' -> 1250.50
        '120,50'   -> 120.50
        '120.50'   -> 120.50
        '0,193400' -> 0.193400
        '-35,00'   -> -35.00
    """
    if not stringa:
        return None
    s = stringa.strip().replace("€", "").replace("EUR", "").replace("eur", "").replace(" ", "")
    # Gestione segno meno
    negativo = s.startswith("-")
    if negativo:
        s = s[1:]

    # Se ci sono sia punto che virgola (es. 1.250,50)
    if "." in s and "," in s:
        # Il punto separa le migliaia, la virgola i decimali
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif "." in s:
        # Se ha più di un punto (es. 1.250.000)
        if s.count(".") > 1:
            s = s.replace(".", "")

    try:
        val = Decimal(s)
        return -val if negativo else val
    except Exception:
        return None


def _parse_data_it(stringa_data: str | None) -> date | None:
    """
    Converte stringhe di data italiane in oggetto date.
    Supporta formati numerici (DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, YYYY-MM-DD)
    e testuali ('15 gennaio 2024', '1 gen 2024', '1° marzo 2024').
    """
    if not stringa_data:
        return None
    s = stringa_data.strip().lower()
    s = re.sub(r"\b1[°º]\b", "1", s)

    # 1. Formati numerici standard
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    # 2. Formato testuale '15 gennaio 2024' o '15 gen 2024'
    match_testo = re.search(r"(\d{1,2})\s+([a-z]{3,9})\s+(\d{4})", s)
    if match_testo:
        giorno = int(match_testo.group(1))
        nome_mese = match_testo.group(2)
        anno = int(match_testo.group(3))
        mese_num = MESI_ITALIANI.get(nome_mese)
        if mese_num and 1 <= giorno <= 31:
            try:
                return date(anno, mese_num, giorno)
            except ValueError:
                pass

    return None


# ===========================================================================
# MOTORE DI ESTRAZIONE MULTIFORNITORE (STRATEGY PATTERN)
# ===========================================================================
def estrai_euristico(testo: str, pagine: list[str] | None = None) -> dict[str, Any]:
    """
    Parser multifornitore avanzato basato su Strategy Pattern e Fallback composito.
    Identifica il fornitore specifico (Enel, Plenitude, SEN, Acea, Octopus, A2A, ecc.)
    e applica le regole euristiche e i pattern dedicati.
    """
    from .parsers.registry import get_parser_registry
    registry = get_parser_registry()
    return registry.parse(testo, pagine)


def get_fornitori_supportati() -> list[dict[str, Any]]:
    """Ritorna l'elenco dei fornitori supportati dal motore multifornitore."""
    from .parsers.registry import get_parser_registry
    return get_parser_registry().get_fornitori_supportati()


# ===========================================================================
# API PRINCIPALE DI ANALISI DOCUMENTO
# ===========================================================================
def analizza_documento(file_obj) -> dict[str, Any]:
    """
    Funzione di ingresso unificata per il parsing di bollette:
    1. Estrae il testo da tutte le pagine del PDF
    2. Esegue il dispatcher multifornitore
    3. Converte date e decimali in formati pronti per JSON e input HTML5
    """
    testo, pagine, err = estrai_testo_pdf(file_obj)
    if err and not testo:
        return {"ok": False, "errore": err}

    dati = estrai_euristico(testo, pagine)

    p_inizio_iso = dati["periodo_inizio"].isoformat() if dati.get("periodo_inizio") else ""
    p_fine_iso = dati["periodo_fine"].isoformat() if dati.get("periodo_fine") else ""
    d_emiss_iso = dati["data_emissione"].isoformat() if dati.get("data_emissione") else ""
    d_scad_iso = dati["data_scadenza"].isoformat() if dati.get("data_scadenza") else ""

    risultato = {
        "ok": True,
        "tipo": dati["tipo"],
        "fornitore": dati["fornitore"],
        "codice_fornitore": dati.get("codice_fornitore", ""),
        "parser_usato": dati.get("parser_usato", "GenericFallbackParser"),
        "fonte_parsing": dati.get("fonte_parsing", "Dedicato"),
        "pod": dati.get("pod", ""),
        "pdr": dati.get("pdr", ""),
        "numero_fattura": dati.get("numero_fattura", ""),
        "data_emissione": dati.get("data_emissione"),
        "data_emissione_iso": d_emiss_iso,
        "data_scadenza": dati.get("data_scadenza"),
        "data_scadenza_iso": d_scad_iso,
        "periodo_inizio": dati.get("periodo_inizio"),
        "periodo_inizio_iso": p_inizio_iso,
        "periodo_fine": dati.get("periodo_fine"),
        "periodo_fine_iso": p_fine_iso,
        "importo_totale": float(dati["importo_totale"]) if dati.get("importo_totale") is not None else None,
        "consumo": float(dati["consumo"]) if dati.get("consumo") is not None else None,
        "fasce": dati.get("fasce", {}),
        "coeff_c": float(dati["coeff_c"]) if dati.get("coeff_c") is not None else None,
        "potere_calorifico_pcs": float(dati["potere_calorifico_pcs"]) if dati.get("potere_calorifico_pcs") is not None else None,
        "spesa_materia": float(dati["spesa_materia"]) if dati.get("spesa_materia") is not None else None,
        "spesa_trasporto": float(dati["spesa_trasporto"]) if dati.get("spesa_trasporto") is not None else None,
        "spesa_oneri": float(dati["spesa_oneri"]) if dati.get("spesa_oneri") is not None else None,
        "spesa_imposte": float(dati["spesa_imposte"]) if dati.get("spesa_imposte") is not None else None,
        "canone_rai": float(dati["canone_rai"]) if dati.get("canone_rai") is not None else None,
        "score": dati.get("score", 0),
        "campi_estratti": dati.get("campi_estratti", []),
        "campi_mancanti": dati.get("campi_mancanti", []),
        "avvisi": dati.get("avvisi", []),
        "anteprima_testo": testo[:600] if testo else "",
    }
    return risultato


# ===========================================================================
# BATCH & BULK IMPORT ENGINE
# ===========================================================================
def importa_file_singolo(file_obj, nome_file: str, sovrascrivi: bool = False) -> dict[str, Any]:
    """
    Analizza, archivia ed elabora un singolo file PDF di bolletta (Luce o Gas).
    Salva il file in media/, calcola Gradi Giorno Open-Meteo e benchmark PUN/PSV.
    """
    from .models import BollettaElettrica, BollettaGas
    from . import services

    if hasattr(file_obj, "seek"):
        file_obj.seek(0)

    dati = analizza_documento(file_obj)
    if not dati.get("ok"):
        return {
            "ok": False,
            "stato": "errore",
            "file": nome_file,
            "errore": dati.get("errore", "Impossibile estrarre dati dal PDF."),
        }

    p_inizio = dati.get("periodo_inizio")
    p_fine = dati.get("periodo_fine")
    if not p_inizio or not p_fine:
        return {
            "ok": False,
            "stato": "errore",
            "file": nome_file,
            "errore": "Date del periodo di fatturazione non rilevate nel documento.",
        }

    tipo = dati.get("tipo", "luce")
    fornitore = dati.get("fornitore") or ("Acea Energia" if tipo == "luce" else "Eni Plenitude")
    totale = dati.get("importo_totale")

    if hasattr(file_obj, "seek"):
        file_obj.seek(0)

    if tipo == "gas":
        esistente = BollettaGas.objects.filter(periodo_inizio=p_inizio, periodo_fine=p_fine).first()
        if esistente and not sovrascrivi:
            return {
                "ok": True,
                "stato": "saltato",
                "tipo": "gas",
                "file": nome_file,
                "id": esistente.pk,
                "fornitore": esistente.fornitore,
                "periodo": f"{esistente.periodo_inizio.strftime('%d/%m/%Y')} → {esistente.periodo_fine.strftime('%d/%m/%Y')}",
                "consumo": f"{esistente.smc_fatturati} Smc",
                "importo": f"{esistente.importo_totale} €" if esistente.importo_totale else "—",
                "messaggio": "Già presente nell'archivio (non sovrascritto).",
            }

        smc = Decimal(str(dati.get("consumo") or 0))
        c_val = Decimal(str(dati.get("coeff_c") or "1.000000"))
        pcs_val = Decimal(str(dati.get("potere_calorifico_pcs") or "0.038520"))

        if not esistente:
            b = BollettaGas(
                fornitore=fornitore,
                pdr=dati.get("pdr") or "",
                numero_fattura=dati.get("numero_fattura") or "",
                data_emissione=dati.get("data_emissione"),
                periodo_inizio=p_inizio,
                periodo_fine=p_fine,
                smc_fatturati=smc,
                importo_totale=Decimal(str(totale)) if totale is not None else None,
                coeff_c=c_val,
                potere_calorifico_pcs=pcs_val,
                quota_materia_prima_smc=Decimal("0.480000"),
                quota_fissa_annua=Decimal("120.00"),
                accisa_smc=Decimal("0.185000"),
            )
        else:
            b = esistente
            b.fornitore = fornitore
            if dati.get("pdr"):
                b.pdr = dati["pdr"]
            if dati.get("numero_fattura"):
                b.numero_fattura = dati["numero_fattura"]
            if dati.get("data_emissione"):
                b.data_emissione = dati["data_emissione"]
            b.smc_fatturati = smc
            if totale is not None:
                b.importo_totale = Decimal(str(totale))

        b.file_bolletta.save(nome_file, file_obj, save=False)
        b.save()
        services.aggiorna_campi_calcolati_gas(b)

        return {
            "ok": True,
            "stato": "creato" if not esistente else "aggiornato",
            "tipo": "gas",
            "file": nome_file,
            "id": b.pk,
            "fornitore": b.fornitore,
            "periodo": f"{b.periodo_inizio.strftime('%d/%m/%Y')} → {b.periodo_fine.strftime('%d/%m/%Y')}",
            "consumo": f"{b.smc_fatturati} Smc",
            "importo": f"{b.importo_totale} €" if b.importo_totale else "—",
            "prezzo_marginale": f"{b.prezzo_marginale_medio_smc} €/Smc",
            "hdd": f"{b.temperatura_media} °C" if b.temperatura_media else "—",
            "messaggio": "Archiviata e analizzata con successo." if not esistente else "Aggiornata con successo.",
        }

    else:
        esistente = BollettaElettrica.objects.filter(periodo_inizio=p_inizio, periodo_fine=p_fine).first()
        if esistente and not sovrascrivi:
            return {
                "ok": True,
                "stato": "saltato",
                "tipo": "luce",
                "file": nome_file,
                "id": esistente.pk,
                "fornitore": esistente.fornitore,
                "periodo": f"{esistente.periodo_inizio.strftime('%d/%m/%Y')} → {esistente.periodo_fine.strftime('%d/%m/%Y')}",
                "consumo": f"{esistente.kwh_fatturati} kWh",
                "importo": f"{esistente.importo_totale} €" if esistente.importo_totale else "—",
                "messaggio": "Già presente nell'archivio (non sovrascritto).",
            }

        kwh = int(dati.get("consumo") or 0)
        if not esistente:
            b = BollettaElettrica(
                fornitore=fornitore,
                pod=dati.get("pod") or "",
                numero_fattura=dati.get("numero_fattura") or "",
                data_emissione=dati.get("data_emissione"),
                periodo_inizio=p_inizio,
                periodo_fine=p_fine,
                kwh_fatturati=kwh,
                importo_totale=Decimal(str(totale)) if totale is not None else None,
                prezzo_marginale_base=Decimal("0.175000"),
                accisa_marginale=Decimal("0.022700"),
                quota_fissa_mensile=Decimal("9.50"),
            )
        else:
            b = esistente
            b.fornitore = fornitore
            if dati.get("pod"):
                b.pod = dati["pod"]
            if dati.get("numero_fattura"):
                b.numero_fattura = dati["numero_fattura"]
            if dati.get("data_emissione"):
                b.data_emissione = dati["data_emissione"]
            b.kwh_fatturati = kwh
            if totale is not None:
                b.importo_totale = Decimal(str(totale))

        b.file_bolletta.save(nome_file, file_obj, save=False)
        b.save()
        services.aggiorna_campi_calcolati(b)

        return {
            "ok": True,
            "stato": "creato" if not esistente else "aggiornato",
            "tipo": "luce",
            "file": nome_file,
            "id": b.pk,
            "fornitore": b.fornitore,
            "periodo": f"{b.periodo_inizio.strftime('%d/%m/%Y')} → {b.periodo_fine.strftime('%d/%m/%Y')}",
            "consumo": f"{b.kwh_fatturati} kWh",
            "importo": f"{b.importo_totale} €" if b.importo_totale else "—",
            "prezzo_marginale": f"{b.prezzo_marginale_medio} €/kWh",
            "hdd": f"{b.temperatura_media} °C" if b.temperatura_media else "—",
            "messaggio": "Archiviata e analizzata con successo." if not esistente else "Aggiornata con successo.",
        }


def elabora_caricamento_massivo(files_list, sovrascrivi: bool = False) -> dict[str, Any]:
    """
    Processa una lista di file PDF caricati in batch.
    Ritorna statistiche complessive e lista dettagliata degli esiti.
    """
    risultati = []
    tot_creati = 0
    tot_aggiornati = 0
    tot_saltati = 0
    tot_errori = 0

    for f in files_list:
        nome = getattr(f, "name", str(f))
        esito = importa_file_singolo(f, nome, sovrascrivi=sovrascrivi)
        risultati.append(esito)

        if not esito.get("ok"):
            tot_errori += 1
        elif esito.get("stato") == "creato":
            tot_creati += 1
        elif esito.get("stato") == "aggiornato":
            tot_aggiornati += 1
        elif esito.get("stato") == "saltato":
            tot_saltati += 1

    return {
        "totale_files": len(files_list),
        "tot_creati": tot_creati,
        "tot_aggiornati": tot_aggiornati,
        "tot_saltati": tot_saltati,
        "tot_errori": tot_errori,
        "dettagli": risultati,
    }

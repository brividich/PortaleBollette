"""
Implementazioni specializzate dei parser per i singoli fornitori energetici italiani.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .base import BaseProviderParser


class EnelParser(BaseProviderParser):
    """Parser specializzato per Enel Energia (Mercato Libero)."""
    nome_fornitore = "Enel Energia"
    codice_fornitore = "enel"
    identificatori = ["enel energia", "enel s.p.a", "enel spa", "enel.it"]
    partita_iva = "06655971007"

    def extract_numero_fattura(self, testo: str) -> str:
        # Enel usa pattern tipo: "Fattura n. 4125098765" o "N. 4125098765"
        m = re.search(r"(?:fattura\s*n(?:umero|[.°])?|n(?:umero|[.°])?)\s*[:\s]*([0-9]{8,14})", testo, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return super().extract_numero_fattura(testo)

    def extract_totale(self, testo: str, prima_pagina: str = "") -> Decimal | None:
        # Enel box: "Totale da pagare: 123,45 €" o "Importo totale da pagare"
        m = re.search(
            r"(?:importo\s*totale\s*da\s*pagare|totale\s*da\s*pagare|totale\s*bolletta)[^0-9€\-]*€?\s*([0-9]{1,4}[.,][0-9]{2})",
            testo, re.IGNORECASE
        )
        if m:
            val = self.parse_numero(m.group(1))
            if val is not None and val > 0:
                return val
        return super().extract_totale(testo, prima_pagina)


class ServizioElettricoNazionaleParser(BaseProviderParser):
    """Parser specializzato per Servizio Elettrico Nazionale (SEN - Maggior Tutela)."""
    nome_fornitore = "Servizio Elettrico Nazionale"
    codice_fornitore = "sen"
    identificatori = [
        "servizio elettrico nazionale", "servizioelettriconazionale",
        "maggior tutela", "servizio di maggior tutela"
    ]
    partita_iva = "09633951000"

    def extract_numero_fattura(self, testo: str) -> str:
        m = re.search(r"(?:fattura\s*n(?:umero|[.°])?|fattura\s*n\.)\s*[:\s]*([0-9\/-]{7,18})", testo, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return super().extract_numero_fattura(testo)

    def extract_totale(self, testo: str, prima_pagina: str = "") -> Decimal | None:
        m = re.search(r"(?:totale\s*da\s*pagare\s*euro|totale\s*della\s*bolletta)[^0-9€\-]*€?\s*([0-9]{1,4}[.,][0-9]{2})", testo, re.IGNORECASE)
        if m:
            val = self.parse_numero(m.group(1))
            if val is not None and val > 0:
                return val
        return super().extract_totale(testo, prima_pagina)


class EniPlenitudeParser(BaseProviderParser):
    """Parser specializzato per Eni Plenitude (Gas Metano e Luce)."""
    nome_fornitore = "Eni Plenitude"
    codice_fornitore = "plenitude"
    identificatori = [
        "eni plenitude", "plenitude", "eni gas e luce",
        "eni gas & luce", "eni s.p.a.", "eni spa"
    ]
    partita_iva = "12300020158"

    def extract_numero_fattura(self, testo: str) -> str:
        # Plenitude: "Fattura N° 2026/GAS/0045612" o "Fattura N° 2026/EL/00123"
        m = re.search(r"(?:fattura\s*n(?:umero|[.°])?|documento\s*n(?:umero|[.°])?)\s*[:\s]*([A-Za-z0-9\/-]{6,26})", testo, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return super().extract_numero_fattura(testo)

    def extract_consumo(self, testo: str, tipo: str) -> Decimal | int | None:
        if tipo == "gas":
            # Plenitude usa spesso "Volume corretto a 15°C" o "SMC fatturati"
            m = re.search(r"(?:smc\s*fatturati|volume\s*corretto|volume\s*standard)[^0-9]*([0-9]+(?:[.,][0-9]+)?)\s*smc", testo, re.IGNORECASE)
            if m:
                val = self.parse_numero(m.group(1))
                if val is not None:
                    return val
        return super().extract_consumo(testo, tipo)


class AceaParser(BaseProviderParser):
    """Parser specializzato per Acea Energia (ADR-002 tariffe marginali)."""
    nome_fornitore = "Acea Energia"
    codice_fornitore = "acea"
    identificatori = ["acea energia", "acea spa", "acea.it", "gruppo acea"]
    partita_iva = "07379831006"

    def extract_numero_fattura(self, testo: str) -> str:
        # Acea: "Numero Documento: 4124991234" o "Fattura n."
        m = re.search(r"(?:numero\s*documento|n(?:umero|[.°])?\s*documento|fattura\s*n(?:umero|[.°])?)[:\s]*([A-Za-z0-9\/-]{6,24})", testo, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return super().extract_numero_fattura(testo)

    def extract_totale(self, testo: str, prima_pagina: str = "") -> Decimal | None:
        """
        Estrae il totale bolletta Acea, dando priorità assoluta a 'TOTALE BOLLETTA'
        (esclude il canone RAI addebitato in 'TOTALE DA PAGARE').
        """
        m_bolletta = re.search(
            r"TOTALE\s+BOLLETTA[^0-9€\-]*€?\s*([0-9]{1,4}[.,][0-9]{2})|([0-9]{1,4}[.,][0-9]{2})\s*€?\s*(?:TOTALE\s+BOLLETTA)",
            testo,
            re.IGNORECASE,
        )
        if m_bolletta:
            val_str = m_bolletta.group(1) or m_bolletta.group(2)
            val = self.parse_numero(val_str)
            if val is not None and val > 0:
                return val
        return super().extract_totale(testo, prima_pagina)

    def extract_scontrino(self, testo: str) -> dict[str, Decimal | None]:
        """
        Estrae le voci economiche chiave dallo 'Scontrino dell'energia':
        - Quota fissa periodo
        - Quota potenza periodo
        - Quota fissa netta complessiva (fissa + potenza)
        - Canone abbonamento TV / RAI
        - TOTALE BOLLETTA (senza canone)
        - TOTALE DA PAGARE (con canone)
        """
        risultato: dict[str, Decimal | None] = {
            "quota_fissa": None,
            "quota_potenza": None,
            "quota_fissa_netta_periodo": None,
            "canone_rai": None,
            "totale_bolletta": None,
            "totale_da_pagare": None,
        }

        # Quota fissa (es. "Quota fissa 2 mesi 14,020000 €/mese 28,04 €" o "Quota fissa ... 28,04 €")
        m_qf = re.search(r"Quota\s+fissa\b[^\n]*?([0-9]+[.,][0-9]{2})\s*€", testo, re.IGNORECASE)
        if m_qf:
            risultato["quota_fissa"] = self.parse_numero(m_qf.group(1))

        # Quota potenza (es. "Quota potenza 3,000 kW per 2 mesi 1,976667 €/kW 11,86 €")
        m_qp = re.search(r"Quota\s+potenza\b[^\n]*?([0-9]+[.,][0-9]{2})\s*€", testo, re.IGNORECASE)
        if m_qp:
            risultato["quota_potenza"] = self.parse_numero(m_qp.group(1))

        # Somma quota fissa netta periodo
        qf = risultato["quota_fissa"] or Decimal("0")
        qp = risultato["quota_potenza"] or Decimal("0")
        if risultato["quota_fissa"] is not None or risultato["quota_potenza"] is not None:
            risultato["quota_fissa_netta_periodo"] = qf + qp

        # Canone RAI
        m_rai = re.search(
            r"(?:Canone\s+di\s+abbonamento\s+alla\s+televisione|Canone\s+TV|Canone\s+RAI)[^\n0-9€\-]*€?\s*([0-9]+[.,][0-9]{2})",
            testo,
            re.IGNORECASE,
        )
        if m_rai:
            risultato["canone_rai"] = self.parse_numero(m_rai.group(1))

        # TOTALE BOLLETTA
        m_tb = re.search(r"TOTALE\s+BOLLETTA[^0-9€\-]*€?\s*([0-9]{1,4}[.,][0-9]{2})", testo, re.IGNORECASE)
        if m_tb:
            risultato["totale_bolletta"] = self.parse_numero(m_tb.group(1))

        # TOTALE DA PAGARE
        m_tp = re.search(
            r"(?:TOTALE\s+DA\s+PAGARE|Importo\s+totale\s+da\s+pagare)[^0-9€\-]*€?\s*([0-9]{1,4}[.,][0-9]{2})",
            testo,
            re.IGNORECASE,
        )
        if m_tp:
            risultato["totale_da_pagare"] = self.parse_numero(m_tp.group(1))

        return risultato

    def extract_letture_e_consumi(self, testo: str) -> list[dict[str, Any]]:
        """
        Estrae i consumi mensili disaggregati dalla sezione 'LETTURE E CONSUMI' di Acea.
        Per ciascun blocco 'Attiva GG/MM/AA GG/MM/AA Rilevato/Stimato' somma i kWh
        delle fasce F1..F6 di tipo 'Effettivo' o 'Stimato'.
        IGNORA le righe cumulative 'Fatturato'.
        Prende solo l'ultimo valore 'NN kWh' della riga (il punto nelle letture è separatore migliaia).
        """
        blocchi: list[dict[str, Any]] = []

        # Trova tutte le intestazioni dei blocchi di rilevazione
        pattern_header = re.compile(
            r"Attiva\s+([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})\s+([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})\s+(Rilevato|Stimato)",
            re.IGNORECASE,
        )
        matches = list(pattern_header.finditer(testo))
        if not matches:
            return blocchi

        # Regex per la singola riga di fascia F1..F6 (ignora esplicitamente Fatturato)
        pattern_riga = re.compile(
            r"\bF[1-6]\b[^\n]*?\b(Effettivo|Stimato)\b[^\n]*?([0-9]+(?:\.[0-9]{3})*)\s*kWh",
            re.IGNORECASE,
        )

        for i, m in enumerate(matches):
            inizio_str = m.group(1)
            fine_str = m.group(2)
            d_ini = self.parse_data(inizio_str)
            d_fin = self.parse_data(fine_str)

            start_pos = m.end()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(testo)
            blocco_testo = testo[start_pos:end_pos]

            righe_match = list(pattern_riga.finditer(blocco_testo))
            if not righe_match:
                continue

            kwh_totale_blocco = 0
            tipi_trovati = set()

            for rm in righe_match:
                tipo_riga = rm.group(1).capitalize()
                tipi_trovati.add(tipo_riga)
                kwh_str = rm.group(2).replace(".", "")
                try:
                    kwh_totale_blocco += int(kwh_str)
                except ValueError:
                    pass

            tipo_blocco = "Stimato" if "Stimato" in tipi_trovati else "Effettivo"

            if d_ini and d_fin and kwh_totale_blocco > 0:
                blocchi.append({
                    "inizio": d_ini.isoformat(),
                    "fine": d_fin.isoformat(),
                    "kwh": kwh_totale_blocco,
                    "tipo": tipo_blocco,
                })

        return blocchi

    def extract_box_offerta(self, testo: str) -> dict[str, Any]:
        """
        Riconosce le due varianti di box offerta Acea:
        1. 'Componente energia + dispacciamento' (fino ad aprile 2026)
        2. 'Corrispettivo per il consumo + CDISPD' (da maggio 2026, delibera ARERA 386/2025)
        """
        m_var1 = re.search(
            r"(Componente\s+energia\s*\+\s*dispacciamento)[^\n0-9€]*([0-9]+[.,][0-9]{3,6})?",
            testo,
            re.IGNORECASE,
        )
        if m_var1:
            val = self.parse_numero(m_var1.group(2)) if m_var1.group(2) else None
            return {
                "trovato": True,
                "variante": "pre_maggio_2026",
                "etichetta": "Componente energia + dispacciamento",
                "valore": val,
            }

        m_var2 = re.search(
            r"(Corrispettivo\s+per\s+il\s+consumo\s*\+\s*CDISPD)[^\n0-9€]*([0-9]+[.,][0-9]{3,6})?",
            testo,
            re.IGNORECASE,
        )
        if m_var2:
            val = self.parse_numero(m_var2.group(2)) if m_var2.group(2) else None
            return {
                "trovato": True,
                "variante": "arera_386_2025",
                "etichetta": "Corrispettivo per il consumo + CDISPD",
                "valore": val,
            }

        return {"trovato": False, "variante": "", "etichetta": "", "valore": None}

    def parse(self, testo: str, pagine: list[str] | None = None) -> dict[str, Any]:
        """Esegue il parsing specializzato per Acea Energia integrando scontrino e consumi mensili."""
        dati = super().parse(testo, pagine)

        # 1. Scontrino dell'energia
        scontrino = self.extract_scontrino(testo)
        dati["scontrino"] = scontrino
        if scontrino.get("totale_bolletta") is not None:
            dati["importo_totale"] = scontrino["totale_bolletta"]
            dati["totale_bolletta"] = scontrino["totale_bolletta"]
        if scontrino.get("totale_da_pagare") is not None:
            dati["totale_da_pagare"] = scontrino["totale_da_pagare"]
        if scontrino.get("quota_fissa_netta_periodo") is not None:
            dati["quota_fissa_netta_periodo"] = scontrino["quota_fissa_netta_periodo"]
        if scontrino.get("canone_rai") is not None:
            dati["canone_rai"] = scontrino["canone_rai"]

        # 2. Letture e consumi mensili disaggregati
        mensili = self.extract_letture_e_consumi(testo)
        if mensili:
            dati["consumi_mensili"] = mensili
            dati["consumo"] = sum(m["kwh"] for m in mensili)

        # 3. Riconoscimento box offerta (varianti ARERA)
        box = self.extract_box_offerta(testo)
        dati["box_offerta"] = box
        if box.get("trovato") and box.get("valore") is not None:
            dati["prezzo_offerta"] = box["valore"]

        dati["score"] = self.calcola_score(dati)
        dati["campi_estratti"] = self.calcola_campi_estratti(dati)
        dati["campi_mancanti"] = self.calcola_campi_mancanti(dati)
        return dati


class A2AParser(BaseProviderParser):
    """Parser specializzato per A2A Energia."""
    nome_fornitore = "A2A Energia"
    codice_fornitore = "a2a"
    identificatori = ["a2a energia", "a2a spa", "gruppo a2a", "a2a.eu"]
    partita_iva = "12883420155"

    def extract_numero_fattura(self, testo: str) -> str:
        m = re.search(r"(?:fattura\s*(?:commerciale)?\s*n(?:umero|[.°])?|doc\.\s*n(?:umero|[.°])?)[:\s]*([A-Za-z0-9\/-]{6,24})", testo, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return super().extract_numero_fattura(testo)


class OctopusParser(BaseProviderParser):
    """Parser specializzato per Octopus Energy."""
    nome_fornitore = "Octopus Energy"
    codice_fornitore = "octopus"
    identificatori = ["octopus energy", "octopus", "octopusenergy.it"]
    partita_iva = "11849890963"

    def extract_numero_fattura(self, testo: str) -> str:
        m = re.search(r"(?:documento\s*n(?:umero|[.°])?|fattura\s*n(?:umero|[.°])?|n(?:umero|[.°])?)\s*[:\s]*(OCT-[A-Za-z0-9\/-]+|[0-9]{6,16})", testo, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return super().extract_numero_fattura(testo)


class EdisonParser(BaseProviderParser):
    """Parser specializzato per Edison Energia."""
    nome_fornitore = "Edison Energia"
    codice_fornitore = "edison"
    identificatori = ["edison energia", "edison next", "edison spa", "edison.it"]
    partita_iva = "08526440154"


class HeraParser(BaseProviderParser):
    """Parser specializzato per Hera Comm."""
    nome_fornitore = "Hera Comm"
    codice_fornitore = "hera"
    identificatori = ["hera comm", "gruppo hera", "hera spa", "heracomm.it"]
    partita_iva = "02221101203"


class IrenParser(BaseProviderParser):
    """Parser specializzato per Iren Mercato."""
    nome_fornitore = "Iren Mercato"
    codice_fornitore = "iren"
    identificatori = ["iren mercato", "iren luce gas", "gruppo iren", "iren spa", "irenlucegas.it"]
    partita_iva = "01178580998"


class SorgeniaParser(BaseProviderParser):
    """Parser specializzato per Sorgenia."""
    nome_fornitore = "Sorgenia"
    codice_fornitore = "sorgenia"
    identificatori = ["sorgenia spa", "sorgenia", "sorgenia.it"]
    partita_iva = "07787620151"


class NeNParser(BaseProviderParser):
    """Parser specializzato per NeN Mercato Libero."""
    nome_fornitore = "NeN"
    codice_fornitore = "nen"
    identificatori = ["nen energia", "nen", "nen mercato", "nen.it"]

    def extract_totale(self, testo: str, prima_pagina: str = "") -> Decimal | None:
        # NeN usa il concetto di "Rata fissa mensile"
        m = re.search(r"(?:la\s*tua\s*rata|rata\s*mensile|rata\s*fissa)[^0-9€\-]*€?\s*([0-9]{1,4}[.,][0-9]{2})", testo, re.IGNORECASE)
        if m:
            val = self.parse_numero(m.group(1))
            if val is not None and val > 0:
                return val
        return super().extract_totale(testo, prima_pagina)


class DolomitiParser(BaseProviderParser):
    """Parser specializzato per Dolomiti Energia."""
    nome_fornitore = "Dolomiti Energia"
    codice_fornitore = "dolomiti"
    identificatori = ["dolomiti energia", "dolomiti", "dolomitienergia.it"]


class EonParser(BaseProviderParser):
    """Parser specializzato per E.ON Energia."""
    nome_fornitore = "E.ON"
    codice_fornitore = "eon"
    identificatori = ["e.on energia", "e.on", "eon energia", "eon", "eon.it"]


class PosteParser(BaseProviderParser):
    """Parser specializzato per Poste Energia."""
    nome_fornitore = "Poste Energia"
    codice_fornitore = "poste"
    identificatori = ["poste energia", "poste italiane", "poste.it"]


class GenericFallbackParser(BaseProviderParser):
    """Parser euristico di fallback con catalogo per oltre 35 brand italiani."""
    nome_fornitore = "Altro Fornitore"
    codice_fornitore = "generic"

    CATALOGO_ESTESO = [
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

    def matches(self, testo: str, prima_pagina: str) -> bool:
        return True  # Fallback universale

    def parse(self, testo: str, pagine: list[str] | None = None) -> dict[str, Any]:
        pagine = pagine or []
        res = super().parse(testo, pagine)

        # Cerca fornitore nel catalogo esteso se non ancora identificato
        t_low = testo.lower()
        p1_low = (pagine[0] if pagine else testo[:1800]).lower()
        for nome, kws in self.CATALOGO_ESTESO:
            if any(k in p1_low for k in kws) or any(k in t_low for k in kws):
                res["fornitore"] = nome
                res["fonte_parsing"] = f"Catalogo ({nome})"
                break
        else:
            match_brand = re.search(
                r"(?:fornitore|societ[àa]\s+di\s+vendita|ragione\s+sociale|venditore)[:\s]+([A-Za-z0-9\s.,&'-]{3,35})",
                testo,
                re.IGNORECASE,
            )
            if match_brand:
                brand = match_brand.group(1).strip().title()
                res["fornitore"] = brand
                res["fonte_parsing"] = f"Rilevato Intestazione ({brand})"

        res["score"] = self.calcola_score(res)
        res["campi_estratti"] = self.calcola_campi_estratti(res)
        res["campi_mancanti"] = self.calcola_campi_mancanti(res)
        return res

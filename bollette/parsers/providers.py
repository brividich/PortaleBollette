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

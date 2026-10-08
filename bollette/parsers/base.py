"""
Classe base astratta e utility per i parser specifici dei fornitori di energia italiani.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger("bollette.parsers.base")

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


class BaseProviderParser:
    """
    Classe base per la strategia di parsing di uno specifico operatore o fornitore energetico.
    Ogni sottoclasse implementa o specializza le regex e le peculiarità di layout del proprio brand.
    """
    nome_fornitore: str = "Generico"
    codice_fornitore: str = "generic"
    identificatori: list[str] = []
    partita_iva: str = ""
    tipo_fornitura_default: str = "luce"  # "luce", "gas" o "misto"

    def matches(self, testo: str, prima_pagina: str) -> bool:
        """Determina se questo parser è deputato a gestire il documento."""
        testo_lower = testo.lower()
        p1_lower = prima_pagina.lower()

        # 1. Controllo P.IVA se presente
        if self.partita_iva and self.partita_iva in testo:
            return True

        # 2. Controllo prima pagina (priorità assoluta frontespizio)
        for ident in self.identificatori:
            if ident.lower() in p1_lower:
                return True

        # 3. Controllo testo globale
        for ident in self.identificatori:
            if ident.lower() in testo_lower:
                return True

        return False

    def parse(self, testo: str, pagine: list[str] | None = None) -> dict[str, Any]:
        """Esegue il parsing della bolletta. Le sottoclassi possono sovrascrivere o arricchire questo metodo."""
        pagine = pagine or []
        tipo = self.determina_tipo_fornitura(testo)
        prima_pagina = pagine[0] if pagine else testo[:1800]

        pod = self.extract_pod(testo)
        pdr = self.extract_pdr(testo)
        numero_fattura = self.extract_numero_fattura(testo)
        data_emissione = self.extract_data_emissione(testo)
        data_scadenza = self.extract_data_scadenza(testo)
        periodo_inizio, periodo_fine = self.extract_periodo(testo)
        importo_totale = self.extract_totale(testo, prima_pagina)
        consumo = self.extract_consumo(testo, tipo)
        fasce = self.extract_fasce(testo) if tipo == "luce" else {}
        coeff_c, pcs = self.extract_parametri_gas(testo) if tipo == "gas" else (None, None)

        # Se il consumo totale è assente ma ci sono F1, F2, F3
        avvisi = []
        if tipo == "luce" and not consumo and len(fasce) == 3:
            consumo = sum(fasce.values())
            avvisi.append("Consumo calcolato dalla somma delle fasce orarie F1+F2+F3.")

        risultato = {
            "tipo": tipo,
            "fornitore": self.nome_fornitore,
            "codice_fornitore": self.codice_fornitore,
            "parser_usato": self.__class__.__name__,
            "fonte_parsing": f"Dedicato ({self.nome_fornitore})",
            "pod": pod,
            "pdr": pdr,
            "numero_fattura": numero_fattura,
            "data_emissione": data_emissione,
            "data_scadenza": data_scadenza,
            "periodo_inizio": periodo_inizio,
            "periodo_fine": periodo_fine,
            "importo_totale": importo_totale,
            "consumo": consumo,
            "fasce": fasce,
            "coeff_c": coeff_c,
            "potere_calorifico_pcs": pcs,
            "avvisi": avvisi,
        }

        risultato["score"] = self.calcola_score(risultato)
        risultato["campi_estratti"] = self.calcola_campi_estratti(risultato)
        risultato["campi_mancanti"] = self.calcola_campi_mancanti(risultato)
        return risultato

    # -----------------------------------------------------------------------
    # UTILITY CONDIVISE DI ESTRAZIONE
    # -----------------------------------------------------------------------
    def parse_numero(self, stringa: str | None) -> Decimal | None:
        if not stringa:
            return None
        s = stringa.strip().replace("€", "").replace("EUR", "").replace("eur", "").replace(" ", "")
        negativo = s.startswith("-")
        if negativo:
            s = s[1:]
        if "." in s and "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        elif "." in s and s.count(".") > 1:
            s = s.replace(".", "")
        try:
            val = Decimal(s)
            return -val if negativo else val
        except Exception:
            return None

    def parse_data(self, stringa_data: str | None) -> date | None:
        if not stringa_data:
            return None
        s = stringa_data.strip().lower()
        s = re.sub(r"\b1[°º]\b", "1", s)
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
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

    def determina_tipo_fornitura(self, testo: str) -> str:
        t_low = testo.lower()
        score_gas = 0
        score_luce = 0
        if re.search(r"\b(IT\s*\d{3}\s*E\s*[A-Z0-9]{8,11})\b", testo, re.IGNORECASE):
            score_luce += 10
        if re.search(r"(?:pdr|punto\s*di\s*riconsegna)[:\s]*([0-9\s]{14,18})", testo, re.IGNORECASE):
            score_gas += 10

        for w in ("gas naturale", "smc", "pdr", "metano", "potere calorifico", "pcs", "coefficiente c"):
            score_gas += t_low.count(w)
        for w in ("energia elettrica", "kwh", "pod", "potenza impegnata", "f1", "f2", "f3", "perdite di rete"):
            score_luce += t_low.count(w)

        return "gas" if score_gas > score_luce else "luce"

    def extract_pod(self, testo: str) -> str:
        pod_match = re.search(r"\b(IT\s*\d{3}\s*E\s*[A-Z0-9]{8,11})\b", testo, re.IGNORECASE)
        if pod_match:
            pod_clean = re.sub(r"\s+", "", pod_match.group(1)).upper()
            if len(pod_clean) in (14, 15):
                return pod_clean
        # Fallback etichetta
        m = re.search(r"(?:codice\s*pod|punto\s*di\s*prelievo|pod)[:\s]*([A-Z0-9\s]{14,20})", testo, re.IGNORECASE)
        if m:
            clean = re.sub(r"\s+", "", m.group(1)).upper()
            if clean.startswith("IT") and len(clean) in (14, 15):
                return clean
        return ""

    def extract_pdr(self, testo: str) -> str:
        pdr_match = re.search(
            r"(?:pdr|punto\s*di\s*riconsegna(?:\s*\(?pdr\)?)?|codice\s*pdr)[:\s]*([0-9\s]{14,18})",
            testo,
            re.IGNORECASE,
        )
        if pdr_match:
            pdr_clean = re.sub(r"\s+", "", pdr_match.group(1))
            if len(pdr_clean) == 14 and pdr_clean.isdigit():
                return pdr_clean
        m = re.search(r"\b([0-9]{14})\b", testo)
        if m:
            return m.group(1)
        return ""

    def extract_numero_fattura(self, testo: str) -> str:
        patterns = [
            r"(?:fattura\s*n(?:umero|[.°])?|documento\s*n(?:umero|[.°])?|n(?:umero|[.°])?\s*fattura)[:\s]*([A-Za-z0-9\/-]{4,30})",
            r"(?:fattura\s*(?:commerciale|elettronica|fiscale)\s*n(?:umero|[.°])?)[:\s]*([A-Za-z0-9\/-]{4,30})",
            r"(?:doc\.\s*n(?:umero|[.°])?)[:\s]*([A-Za-z0-9\/-]{4,30})",
        ]
        for p in patterns:
            m = re.search(p, testo, re.IGNORECASE)
            if m:
                val = m.group(1).strip().strip(".-/,")
                if len(val) >= 4 and not val.lower() in ("del", "data", "elettronica", "commerciale"):
                    return val
        return ""

    def extract_data_emissione(self, testo: str) -> date | None:
        patterns = [
            r"(?:data\s*(?:di\s*)?emissione|data\s*fattura|data\s*documento|emessa\s*il|data\s*di\s*creazione)[:\s]*([0-9]{1,2}[/.-][0-9]{1,2}[/.-][0-9]{4})",
            r"(?:data\s*(?:di\s*)?emissione|data\s*fattura)[:\s]*([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+[0-9]{4})",
        ]
        for p in patterns:
            m = re.search(p, testo, re.IGNORECASE)
            if m:
                d = self.parse_data(m.group(1))
                if d:
                    return d
        return None

    def extract_data_scadenza(self, testo: str) -> date | None:
        patterns = [
            r"(?:scadenza|entro\s*il|data\s*scadenza|pagare\s*entro\s*il)[:\s]*([0-9]{1,2}[/.-][0-9]{1,2}[/.-][0-9]{4})",
            r"(?:scadenza|entro\s*il)[:\s]*([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+[0-9]{4})",
        ]
        for p in patterns:
            m = re.search(p, testo, re.IGNORECASE)
            if m:
                d = self.parse_data(m.group(1))
                if d:
                    return d
        return None

    def extract_periodo(self, testo: str) -> tuple[date | None, date | None]:
        patterns = [
            r"(?:periodo|dal)\s*(?:di\s*fatturazione|di\s*competenza|di\s*riferimento|consumi)?[:\s]*(?:dal\s*)?([0-9]{1,2}[/.-][0-9]{1,2}[/.-][0-9]{4})\s*(?:al|–|-|fino al)\s*([0-9]{1,2}[/.-][0-9]{1,2}[/.-][0-9]{4})",
            r"(?:fatturazione|competenza)[:\s]*([0-9]{1,2}[/.-][0-9]{1,2}[/.-][0-9]{4})\s*-\s*([0-9]{1,2}[/.-][0-9]{1,2}[/.-][0-9]{4})",
        ]
        for p in patterns:
            m = re.search(p, testo, re.IGNORECASE)
            if m:
                d1 = self.parse_data(m.group(1))
                d2 = self.parse_data(m.group(2))
                if d1 and d2:
                    return (d2, d1) if d1 > d2 else (d1, d2)

        # Pattern testuale 'dal 1 Gennaio 2026 al 28 Febbraio 2026'
        p_testo = re.search(
            r"dal\s*([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+[0-9]{4})\s*al\s*([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+[0-9]{4})",
            testo,
            re.IGNORECASE,
        )
        if p_testo:
            d1 = self.parse_data(p_testo.group(1))
            d2 = self.parse_data(p_testo.group(2))
            if d1 and d2:
                return (d2, d1) if d1 > d2 else (d1, d2)

        # Pattern mese di competenza singolo
        p_mese = re.search(
            r"(?:mese\s*di\s*competenza|competenza\s*mese)[:\s]*([a-zA-Z]{3,9})\s+(\d{4})",
            testo,
            re.IGNORECASE,
        )
        if p_mese:
            m_nome = p_mese.group(1).lower()
            anno_val = int(p_mese.group(2))
            m_num = MESI_ITALIANI.get(m_nome)
            if m_num:
                giorni_max = 29 if m_num == 2 and (anno_val % 4 == 0) else GIORNI_PER_MESE[m_num]
                return date(anno_val, m_num, 1), date(anno_val, m_num, giorni_max)

        return None, None

    def extract_totale(self, testo: str, prima_pagina: str = "") -> Decimal | None:
        patterns = [
            r"(?:totale da pagare|totale bolletta|importo da pagare|totale fattura|totale documento|totale da saldare|importo a debito|saldo da pagare|netto da pagare)[^0-9€\-]*€?\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+[.,][0-9]{2})",
            r"(?:totale da pagare|totale bolletta|totale fattura)[^0-9€\-]*([0-9]{1,4}[.,][0-9]{2})\s*€",
            r"€\s*([0-9]{1,4}[.,][0-9]{2})\s*(?:totale|da pagare)",
        ]
        for p in patterns:
            m = re.search(p, testo, re.IGNORECASE)
            if m:
                val = self.parse_numero(m.group(1))
                if val is not None and val > 0:
                    return val

        # Note di credito
        m_credito = re.search(
            r"(?:totale a credito|importo a vostro credito|a credito)[^0-9€\-]*€?\s*-?\s*([0-9]+[.,][0-9]{2})",
            testo,
            re.IGNORECASE,
        )
        if m_credito:
            val = self.parse_numero(m_credito.group(1))
            if val:
                return -val

        # Fallback prima pagina
        if prima_pagina:
            m_p1 = re.search(r"€\s*([0-9]{1,4}[.,][0-9]{2})\b", prima_pagina)
            if m_p1:
                return self.parse_numero(m_p1.group(1))

        return None

    def extract_consumo(self, testo: str, tipo: str) -> Decimal | int | None:
        if tipo == "luce":
            patterns_kwh = [
                r"(?:energia fatturata|consumo fatturato|totale consumi|consumo totale|totale energia attiva|energia prelevata)[^0-9]*([0-9]+(?:\.[0-9]{3})?)\s*kwh",
                r"([0-9]+(?:\.[0-9]{3})?)\s*kwh\s*(?:fatturati|prelevati|consumati)",
                r"totale\s*kwh[:\s]*([0-9]+(?:\.[0-9]{3})?)",
            ]
            for p in patterns_kwh:
                m = re.search(p, testo, re.IGNORECASE)
                if m:
                    val_clean = m.group(1).replace(".", "")
                    try:
                        return int(val_clean)
                    except ValueError:
                        pass
            return None
        else:
            patterns_smc = [
                r"(?:smc fatturati|consumo fatturato|totale smc|consumo totale|volume standard fatturato|volume corretto)[^0-9]*([0-9]+(?:[.,][0-9]+)?)\s*smc",
                r"([0-9]+(?:[.,][0-9]+)?)\s*smc\s*(?:fatturati|consumati)",
                r"totale\s*smc[:\s]*([0-9]+(?:[.,][0-9]+)?)",
            ]
            for p in patterns_smc:
                m = re.search(p, testo, re.IGNORECASE)
                if m:
                    val = self.parse_numero(m.group(1))
                    if val is not None:
                        return val
            return None

    def extract_fasce(self, testo: str) -> dict[str, int]:
        fasce: dict[str, int] = {}
        for f in ("F1", "F2", "F3"):
            m = re.search(rf"\b{f}[:\s]*([0-9]+(?:\.[0-9]{3})?)\s*kwh", testo, re.IGNORECASE)
            if m:
                fasce[f.lower()] = int(m.group(1).replace(".", ""))
        return fasce

    def extract_parametri_gas(self, testo: str) -> tuple[Decimal | None, Decimal | None]:
        coeff_c = None
        pcs = None
        m_c = re.search(
            r"(?:coefficiente\s*(?:di\s*conversione|correttivo)?\s*c|coeff\.?\s*c)[:\s=]*([0-9]+[.,][0-9]{3,6})",
            testo,
            re.IGNORECASE,
        )
        if m_c:
            coeff_c = self.parse_numero(m_c.group(1))
        m_p = re.search(
            r"(?:pcs|potere\s*calorifico\s*superiore)[:\s=]*([0-9]+[.,][0-9]{4,6})",
            testo,
            re.IGNORECASE,
        )
        if m_p:
            pcs = self.parse_numero(m_p.group(1))
        return coeff_c, pcs

    def calcola_score(self, d: dict[str, Any]) -> int:
        score = 0
        if d.get("fornitore") and d["fornitore"] != "Altro Fornitore":
            score += 20
        if d.get("periodo_inizio") and d.get("periodo_fine"):
            score += 25
        if d.get("importo_totale") is not None:
            score += 25
        if d.get("consumo") is not None:
            score += 20
        if d.get("pod") or d.get("pdr"):
            score += 5
        if d.get("numero_fattura"):
            score += 5
        return min(100, score)

    def calcola_campi_estratti(self, d: dict[str, Any]) -> list[str]:
        campi = []
        if d.get("fornitore") and d["fornitore"] != "Altro Fornitore":
            campi.append("Fornitore")
        if d.get("periodo_inizio") and d.get("periodo_fine"):
            campi.append("Periodo fatturazione")
        if d.get("importo_totale") is not None:
            campi.append("Importo totale")
        if d.get("consumo") is not None:
            campi.append("Consumo (" + ("kWh" if d.get("tipo") == "luce" else "Smc") + ")")
        if d.get("pod"):
            campi.append("POD")
        if d.get("pdr"):
            campi.append("PDR")
        if d.get("numero_fattura"):
            campi.append("Numero fattura")
        return campi

    def calcola_campi_mancanti(self, d: dict[str, Any]) -> list[str]:
        mancanti = []
        if not d.get("periodo_inizio") or not d.get("periodo_fine"):
            mancanti.append("Periodo fatturazione")
        if d.get("importo_totale") is None:
            mancanti.append("Importo totale")
        if d.get("consumo") is None:
            mancanti.append("Consumo")
        if d.get("tipo") == "luce" and not d.get("pod"):
            mancanti.append("POD")
        elif d.get("tipo") == "gas" and not d.get("pdr"):
            mancanti.append("PDR")
        if not d.get("numero_fattura"):
            mancanti.append("Numero fattura")
        return mancanti

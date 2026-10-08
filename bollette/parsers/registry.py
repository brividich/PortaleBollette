"""
Registro centrale e Dispatcher per l'architettura dei parser multifornitore.
"""
from __future__ import annotations

import logging
from typing import Any

from .base import BaseProviderParser
from .providers import (
    EnelParser,
    ServizioElettricoNazionaleParser,
    EniPlenitudeParser,
    AceaParser,
    A2AParser,
    OctopusParser,
    EdisonParser,
    HeraParser,
    IrenParser,
    SorgeniaParser,
    NeNParser,
    DolomitiParser,
    EonParser,
    PosteParser,
    GenericFallbackParser,
)

logger = logging.getLogger("bollette.parsers.registry")


class MultiFornitoreRegistry:
    """
    Registro e Dispatcher per la selezione dinamica del parser più idoneo.
    Implementa lo Strategy Pattern con Fallback composito.
    """

    def __init__(self):
        # Lista ordinata di parser specializzati (in ordine di priorità/specificità)
        self.parsers_specializzati: list[BaseProviderParser] = [
            EnelParser(),
            ServizioElettricoNazionaleParser(),
            EniPlenitudeParser(),
            AceaParser(),
            A2AParser(),
            OctopusParser(),
            EdisonParser(),
            HeraParser(),
            IrenParser(),
            SorgeniaParser(),
            NeNParser(),
            DolomitiParser(),
            EonParser(),
            PosteParser(),
        ]
        self.fallback_parser: BaseProviderParser = GenericFallbackParser()

    def rileva_parser(self, testo: str, prima_pagina: str = "") -> BaseProviderParser:
        """Individua il parser più adatto per il documento in ingresso."""
        if not prima_pagina:
            prima_pagina = testo[:1800]

        # 1. Cerca nei parser specializzati
        for parser in self.parsers_specializzati:
            if parser.matches(testo, prima_pagina):
                return parser

        # 2. Fallback su parser generico
        return self.fallback_parser

    def parse(self, testo: str, pagine: list[str] | None = None) -> dict[str, Any]:
        """
        Esegue il parsing con strategia multifornitore:
        1. Rileva il parser dedicato
        2. Esegue l'estrazione
        3. Se qualche campo fondamentale manca, esegue fallback composito con il parser generico
        """
        pagine = pagine or []
        prima_pagina = pagine[0] if pagine else testo[:1800]

        parser_selezionato = self.rileva_parser(testo, prima_pagina)
        dati = parser_selezionato.parse(testo, pagine)

        # Se il parser specializzato non ha trovato periodo o totale, interroga il fallback per colmare eventuali gap
        if not dati.get("periodo_inizio") or dati.get("importo_totale") is None or dati.get("consumo") is None:
            if not isinstance(parser_selezionato, GenericFallbackParser):
                dati_fallback = self.fallback_parser.parse(testo, pagine)
                if not dati.get("periodo_inizio") and dati_fallback.get("periodo_inizio"):
                    dati["periodo_inizio"] = dati_fallback["periodo_inizio"]
                    dati["periodo_fine"] = dati_fallback["periodo_fine"]
                    dati.setdefault("avvisi", []).append("Periodo completato tramite fallback euristico.")
                if dati.get("importo_totale") is None and dati_fallback.get("importo_totale") is not None:
                    dati["importo_totale"] = dati_fallback["importo_totale"]
                    dati.setdefault("avvisi", []).append("Importo completato tramite fallback euristico.")
                if dati.get("consumo") is None and dati_fallback.get("consumo") is not None:
                    dati["consumo"] = dati_fallback["consumo"]
                    dati.setdefault("avvisi", []).append("Consumo completato tramite fallback euristico.")
                if not dati.get("numero_fattura") and dati_fallback.get("numero_fattura"):
                    dati["numero_fattura"] = dati_fallback["numero_fattura"]

                # Ricalcola score e campi
                dati["score"] = parser_selezionato.calcola_score(dati)
                dati["campi_estratti"] = parser_selezionato.calcola_campi_estratti(dati)
                dati["campi_mancanti"] = parser_selezionato.calcola_campi_mancanti(dati)

        return dati

    def get_fornitori_supportati(self) -> list[dict[str, Any]]:
        """Ritorna l'elenco di tutti i fornitori supportati dall'architettura."""
        elenco = []
        for p in self.parsers_specializzati:
            elenco.append({
                "nome": p.nome_fornitore,
                "codice": p.codice_fornitore,
                "classe": p.__class__.__name__,
                "tipo": "Dedicato",
            })
        if isinstance(self.fallback_parser, GenericFallbackParser):
            for nome, _ in self.fallback_parser.CATALOGO_ESTESO:
                elenco.append({
                    "nome": nome,
                    "codice": nome.lower().replace(" ", "_"),
                    "classe": "GenericFallbackParser",
                    "tipo": "Catalogo",
                })
        return elenco


# Singleton del registro
_REGISTRY_INSTANCE: MultiFornitoreRegistry | None = None


def get_parser_registry() -> MultiFornitoreRegistry:
    """Ritorna l'istanza singleton del registro dei parser."""
    global _REGISTRY_INSTANCE
    if _REGISTRY_INSTANCE is None:
        _REGISTRY_INSTANCE = MultiFornitoreRegistry()
    return _REGISTRY_INSTANCE

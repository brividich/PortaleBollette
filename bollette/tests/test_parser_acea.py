"""
Test suite per la Fase 2: ripartizione consumi per mese solare e parser specializzato Acea Energia.
"""
from datetime import date
from decimal import Decimal
from pathlib import Path
from django.test import SimpleTestCase

from bollette.fiscalita import ripartisci_consumi_per_mese
from bollette.parsers.providers import AceaParser

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class RipartizioneConsumiTests(SimpleTestCase):
    """Test della ripartizione dei consumi per singolo mese solare (esplicita vs pro-rata)."""

    def test_ripartizione_con_consumi_mensili_effettivi(self):
        """Bolletta B con consumi mensili estratti da letture: [205, 272] effettivi."""
        c_mensili = [
            {"inizio": "2026-05-01", "fine": "2026-05-31", "kwh": 205, "tipo": "Effettivo"},
            {"inizio": "2026-06-01", "fine": "2026-06-30", "kwh": 272, "tipo": "Effettivo"},
        ]
        res = ripartisci_consumi_per_mese(
            periodo_inizio=date(2026, 5, 1),
            periodo_fine=date(2026, 6, 30),
            kwh_totali=477,
            consumi_mensili=c_mensili,
        )
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0][2], Decimal("205"))
        self.assertFalse(res[0][3])  # stimato=False
        self.assertEqual(res[1][2], Decimal("272"))
        self.assertFalse(res[1][3])  # stimato=False

    def test_ripartizione_prorata_degradata_bolletta_b(self):
        """
        Bolletta B senza dettaglio mensile: pro-rata 31 gg (maggio) e 30 gg (giugno).
        Formula: 477 * 31 / 61 = 242; resto 477 - 242 = 235.
        Entrambi con flag stimato=True.
        """
        res = ripartisci_consumi_per_mese(
            periodo_inizio=date(2026, 5, 1),
            periodo_fine=date(2026, 6, 30),
            kwh_totali=477,
            consumi_mensili=None,
        )
        self.assertEqual(len(res), 2)
        # Maggio (31 gg su 61)
        self.assertEqual(res[0][0], date(2026, 5, 1))
        self.assertEqual(res[0][1], date(2026, 5, 31))
        self.assertEqual(res[0][2], Decimal("242"))
        self.assertTrue(res[0][3])  # stimato=True

        # Giugno (30 gg su 61, residuo esatto)
        self.assertEqual(res[1][0], date(2026, 6, 1))
        self.assertEqual(res[1][1], date(2026, 6, 30))
        self.assertEqual(res[1][2], Decimal("235"))
        self.assertTrue(res[1][3])  # stimato=True

        # Somma rigorosa
        self.assertEqual(res[0][2] + res[1][2], Decimal("477"))


class ParserAceaSpecializzatoTests(SimpleTestCase):
    """Test del parser Acea Energia su scontrino energia, letture e box offerta."""

    def setUp(self):
        self.parser = AceaParser()

    def test_parsing_scontrino_e_letture_bolletta_c(self):
        """Verifica estrazione completa da snippet sintetico anonimo di Bolletta C."""
        testo = (FIXTURES_DIR / "bolletta_c_scontrino.txt").read_text(encoding="utf-8")
        dati = self.parser.parse(testo)

        # 1. Consumi mensili disaggregati
        mensili = dati.get("consumi_mensili", [])
        self.assertEqual(len(mensili), 2)
        self.assertEqual(mensili[0]["kwh"], 335)  # 103 + 124 + 108
        self.assertEqual(mensili[0]["tipo"], "Effettivo")
        self.assertEqual(mensili[1]["kwh"], 334)  # 105 + 117 + 112
        self.assertEqual(mensili[1]["tipo"], "Effettivo")
        self.assertEqual(dati["consumo"], 669)

        # 2. Scontrino dell'energia
        scontrino = dati.get("scontrino", {})
        self.assertEqual(scontrino.get("quota_fissa"), Decimal("28.04"))
        self.assertEqual(scontrino.get("quota_potenza"), Decimal("11.86"))
        self.assertEqual(scontrino.get("quota_fissa_netta_periodo"), Decimal("39.90"))
        self.assertEqual(scontrino.get("canone_rai"), Decimal("18.00"))

        # 3. Importo totale = TOTALE BOLLETTA (e NON TOTALE DA PAGARE)
        self.assertEqual(dati["importo_totale"], Decimal("198.66"))
        self.assertEqual(dati["totale_bolletta"], Decimal("198.66"))
        self.assertEqual(dati["totale_da_pagare"], Decimal("216.66"))
        self.assertNotEqual(dati["importo_totale"], Decimal("216.66"))

    def test_tolleranza_box_offerta_aprile_2026(self):
        """Verifica riconoscimento etichetta 'Componente energia + dispacciamento'."""
        testo = (FIXTURES_DIR / "box_offerta_aprile.txt").read_text(encoding="utf-8")
        dati = self.parser.parse(testo)
        box = dati.get("box_offerta", {})
        self.assertTrue(box.get("trovato"))
        self.assertIn("dispacciamento", box.get("etichetta", "").lower())
        self.assertEqual(box.get("valore"), Decimal("0.142500"))
        self.assertEqual(dati["importo_totale"], Decimal("61.45"))

    def test_tolleranza_box_offerta_maggio_2026_cdispd(self):
        """Verifica riconoscimento etichetta 'Corrispettivo per il consumo + CDISPD' (ARERA 386/2025)."""
        testo = (FIXTURES_DIR / "box_offerta_maggio_cdispd.txt").read_text(encoding="utf-8")
        dati = self.parser.parse(testo)
        box = dati.get("box_offerta", {})
        self.assertTrue(box.get("trovato"))
        self.assertIn("cdispd", box.get("etichetta", "").lower())
        self.assertEqual(box.get("valore"), Decimal("0.138000"))
        self.assertEqual(dati["importo_totale"], Decimal("142.37"))

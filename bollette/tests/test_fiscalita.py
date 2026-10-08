"""
Test suite per il modulo puro bollette.fiscalita (ADR-006: modello accisa a tre tratti).
Verifica esaustiva dei valori attesi analitici e delle tre bollette golden reali Acea.
"""
from decimal import Decimal, ROUND_HALF_UP
from django.test import SimpleTestCase

from bollette.fiscalita import (
    imposte_mese,
    kwh_tassabili,
    pendenza_accisa,
    prezzo_marginale_al_consumo,
    quota_fissa_giornaliera,
    totale_bolletta_luce,
)


class FiscalitaPuraTests(SimpleTestCase):
    """Test delle funzioni pure del modello fiscale elettrico."""

    def test_kwh_tassabili_valori_attesi(self):
        """Verifica kwh_tassabili su tutti i punti di scaglione definiti."""
        casi = [
            (150, Decimal("0")),
            (198, Decimal("48")),
            (205, Decimal("55")),
            (220, Decimal("70")),
            (221, Decimal("72")),
            (223, Decimal("76")),
            (272, Decimal("174")),
            (335, Decimal("300")),
            (370, Decimal("370")),
            (400, Decimal("400")),
        ]
        for kwh, atteso in casi:
            with self.subTest(kwh=kwh):
                risultato = kwh_tassabili(kwh)
                self.assertEqual(risultato, atteso)

    def test_imposte_mese_valori_attesi(self):
        """Verifica riga accisa e riga recupero arrotondate separatamente a 2 decimali."""
        casi = [
            (150, Decimal("0.00"), Decimal("0.00")),
            (198, Decimal("1.09"), Decimal("0.00")),
            (205, Decimal("1.25"), Decimal("0.00")),
            (220, Decimal("1.59"), Decimal("0.00")),
            (221, Decimal("1.61"), Decimal("0.02")),
            (223, Decimal("1.66"), Decimal("0.07")),
            (272, Decimal("2.77"), Decimal("1.18")),
            (334, Decimal("4.18"), Decimal("2.59")),
            (335, Decimal("4.20"), Decimal("2.61")),
        ]
        for kwh, accisa_attesa, recupero_atteso in casi:
            with self.subTest(kwh=kwh):
                d = imposte_mese(kwh)
                self.assertEqual(d["accisa"], accisa_attesa)
                self.assertEqual(d["recupero"], recupero_atteso)

    def test_pendenza_accisa(self):
        """Verifica la derivata marginale a scaglioni (0, 1, 2, 1)."""
        self.assertEqual(pendenza_accisa(0), 0)
        self.assertEqual(pendenza_accisa(150), 0)
        self.assertEqual(pendenza_accisa(151), 1)
        self.assertEqual(pendenza_accisa(200), 1)
        self.assertEqual(pendenza_accisa(220), 1)
        self.assertEqual(pendenza_accisa(221), 2)
        self.assertEqual(pendenza_accisa(300), 2)
        self.assertEqual(pendenza_accisa(369), 2)
        self.assertEqual(pendenza_accisa(370), 1)
        self.assertEqual(pendenza_accisa(500), 1)

    def test_prezzo_marginale_al_consumo_agosto_2026(self):
        """
        Verifica prezzo marginale del prossimo kWh con tariffe agosto 2026:
        base IVA incl. 0,198056, accisa IVA incl. 0,02497.
        Atteso a 4 decimali: k ≤ 150 → 0,1981; 150 < k ≤ 220 → 0,2230; 220 < k < 370 → 0,2480.
        """
        base = Decimal("0.198056")
        accisa = Decimal("0.02497")

        # k ≤ 150
        p_sotto = prezzo_marginale_al_consumo(base, accisa, 100)
        self.assertEqual(p_sotto.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), Decimal("0.1981"))

        # 150 < k ≤ 220
        p_tratto1 = prezzo_marginale_al_consumo(base, accisa, 200)
        self.assertEqual(p_tratto1.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), Decimal("0.2230"))

        # 220 < k < 370
        p_tratto2 = prezzo_marginale_al_consumo(base, accisa, 300)
        self.assertEqual(p_tratto2.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), Decimal("0.2480"))

    def test_bolletta_golden_a_aprile_2026(self):
        """Bolletta A: Aprile 2026 (198 kWh, 30 giorni)."""
        tot = totale_bolletta_luce(
            vendita=Decimal("38.01"),
            rete=Decimal("10.77"),
            oneri=Decimal("5.99"),
            kwh_per_mese=[198],
        )
        self.assertEqual(tot["accisa"], Decimal("1.09"))
        self.assertEqual(tot["recupero"], Decimal("0.00"))
        self.assertEqual(tot["imponibile"], Decimal("55.86"))
        self.assertEqual(tot["iva"], Decimal("5.59"))
        self.assertEqual(tot["totale"], Decimal("61.45"))

        qf = quota_fissa_giornaliera(quota_fissa_netta_periodo=Decimal("19.95"), giorni=30)
        self.assertEqual(qf, Decimal("0.732"))

    def test_bolletta_golden_b_maggio_giugno_2026(self):
        """Bolletta B: Maggio-Giugno 2026 ([205, 272] kWh, 61 giorni)."""
        tot = totale_bolletta_luce(
            vendita=Decimal("87.06"),
            rete=Decimal("22.73"),
            oneri=Decimal("14.44"),
            kwh_per_mese=[205, 272],
        )
        self.assertEqual(tot["accisa"], Decimal("4.02"))
        self.assertEqual(tot["recupero"], Decimal("1.18"))
        self.assertEqual(tot["imponibile"], Decimal("129.43"))
        self.assertEqual(tot["iva"], Decimal("12.94"))
        self.assertEqual(tot["totale"], Decimal("142.37"))

        qf = quota_fissa_giornaliera(quota_fissa_netta_periodo=Decimal("39.90"), giorni=61)
        self.assertEqual(qf, Decimal("0.720"))

    def test_bolletta_golden_c_luglio_agosto_2026(self):
        """Bolletta C: Luglio-Agosto 2026 ([335, 334] kWh, 62 giorni)."""
        tot = totale_bolletta_luce(
            vendita=Decimal("119.30"),
            rete=Decimal("25.54"),
            oneri=Decimal("22.18"),
            kwh_per_mese=[335, 334],
        )
        self.assertEqual(tot["accisa"], Decimal("8.38"))
        self.assertEqual(tot["recupero"], Decimal("5.20"))
        self.assertEqual(tot["imponibile"], Decimal("180.60"))
        self.assertEqual(tot["iva"], Decimal("18.06"))
        self.assertEqual(tot["totale"], Decimal("198.66"))

        qf = quota_fissa_giornaliera(quota_fissa_netta_periodo=Decimal("39.90"), giorni=62)
        self.assertEqual(qf, Decimal("0.708"))

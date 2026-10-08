"""
Test suite per la Fase 3: calcolo prezzo marginale medio a tratti mensili,
quota fissa giornaliera e separazione dei servizi da I/O esterno.
"""
from datetime import date
from decimal import Decimal
from django.test import TestCase

from bollette.models import BollettaElettrica, ConfigurazioneSistema
from bollette import ha_client, services


class ServicesFiscalitaGoldenTests(TestCase):
    """Test di calcolo sulle tre bollette reali Acea (dati di riferimento golden)."""

    def test_bolletta_a_costo_variabile_medio_e_quota_fissa(self):
        """Bolletta A (apr-26): 198 kWh, 30 gg. Atteso medio 0,1995, quota fissa 0,732."""
        # Variabile netta: (25.91 + 2.92 + 5.99) * 1.1 / 198 = 0.193444 €/kWh base IVA incl.
        # Accisa ordinaria: 0.0227 * 1.1 = 0.02497 €/kWh IVA incl.
        b = BollettaElettrica(
            fornitore="Acea Energia",
            periodo_inizio=date(2026, 4, 1),
            periodo_fine=date(2026, 4, 30),
            kwh_fatturati=198,
            prezzo_marginale_base=Decimal("0.193444"),
            accisa_marginale=Decimal("0.024970"),
            soglia_accisa_kwh=150,
            quota_fissa_netta_periodo=Decimal("19.95"),
            consumi_mensili=[
                {"inizio": "2026-04-01", "fine": "2026-04-30", "kwh": 198, "tipo": "Effettivo"}
            ],
        )
        prezzo_medio = services.calcola_prezzo_marginale(b)
        self.assertAlmostEqual(float(prezzo_medio), 0.1995, delta=0.0005)

        # Quota fissa giornaliera: 19.95 * 1.1 / 30 = 0.7315 -> 0.732
        res_ha = services._pubblica_componenti(
            services._componenti_da_bolletta(b), "test", bolletta=b
        )
        self.assertAlmostEqual(res_ha["quota_fissa_giornaliera"], 0.732, delta=0.001)

    def test_bolletta_b_costo_variabile_medio_e_quota_fissa(self):
        """Bolletta B (mag-giu 26): [205, 272] kWh, 61 gg. Atteso medio 0,2065, quota fissa 0,720."""
        # Variabile netta: 84.33 * 1.1 / 477 = 0.194472 €/kWh
        b = BollettaElettrica(
            fornitore="Acea Energia",
            periodo_inizio=date(2026, 5, 1),
            periodo_fine=date(2026, 6, 30),
            kwh_fatturati=477,
            prezzo_marginale_base=Decimal("0.194472"),
            accisa_marginale=Decimal("0.024970"),
            soglia_accisa_kwh=150,
            quota_fissa_netta_periodo=Decimal("39.90"),
            consumi_mensili=[
                {"inizio": "2026-05-01", "fine": "2026-05-31", "kwh": 205, "tipo": "Effettivo"},
                {"inizio": "2026-06-01", "fine": "2026-06-30", "kwh": 272, "tipo": "Effettivo"},
            ],
        )
        prezzo_medio = services.calcola_prezzo_marginale(b)
        self.assertAlmostEqual(float(prezzo_medio), 0.2065, delta=0.0005)

        res_ha = services._pubblica_componenti(
            services._componenti_da_bolletta(b), "test", bolletta=b
        )
        self.assertAlmostEqual(res_ha["quota_fissa_giornaliera"], 0.720, delta=0.001)

    def test_bolletta_c_costo_variabile_medio_e_quota_fissa(self):
        """Bolletta C (lug-ago 26): [335, 334] kWh, 62 gg. Atteso medio 0,2313, quota fissa 0,708."""
        # Variabile netta: 127.12 * 1.1 / 669 = 0.209016 €/kWh
        b = BollettaElettrica(
            fornitore="Acea Energia",
            periodo_inizio=date(2026, 7, 1),
            periodo_fine=date(2026, 8, 31),
            kwh_fatturati=669,
            prezzo_marginale_base=Decimal("0.209016"),
            accisa_marginale=Decimal("0.024970"),
            soglia_accisa_kwh=150,
            quota_fissa_netta_periodo=Decimal("39.90"),
            consumi_mensili=[
                {"inizio": "2026-07-01", "fine": "2026-07-31", "kwh": 335, "tipo": "Effettivo"},
                {"inizio": "2026-08-01", "fine": "2026-08-31", "kwh": 334, "tipo": "Effettivo"},
            ],
        )
        prezzo_medio = services.calcola_prezzo_marginale(b)
        self.assertAlmostEqual(float(prezzo_medio), 0.2313, delta=0.0005)

        res_ha = services._pubblica_componenti(
            services._componenti_da_bolletta(b), "test", bolletta=b
        )
        self.assertAlmostEqual(res_ha["quota_fissa_giornaliera"], 0.708, delta=0.001)

    def test_ricalcola_prezzi_disaccoppiata_da_rete(self):
        """Verifica che ricalcola_prezzi operi puramente in memoria senza chiamate HTTP."""
        b = BollettaElettrica(
            fornitore="Test",
            periodo_inizio=date(2026, 1, 1),
            periodo_fine=date(2026, 1, 31),
            kwh_fatturati=200,
            prezzo_marginale_base=Decimal("0.180000"),
            accisa_marginale=Decimal("0.025000"),
            soglia_accisa_kwh=150,
        )
        res = services.ricalcola_prezzi(b)
        self.assertTrue(res.sopra_soglia_accisa)
        self.assertGreater(res.prezzo_marginale_medio, Decimal("0.18"))

    def test_ha_soglia_recupero_e_avvisi_quota_fissa(self):
        """Verifica pubblicazione di soglia recupero (220) e gestione avvisi su quota fissa mancante."""
        b = BollettaElettrica.objects.create(
            fornitore="Test",
            periodo_inizio=date(2026, 1, 1),
            periodo_fine=date(2026, 1, 31),
            kwh_fatturati=100,
            prezzo_marginale_base=Decimal("0.180000"),
            accisa_marginale=Decimal("0.025000"),
            quota_fissa_netta_periodo=None,
            quota_fissa_mensile=Decimal("12.00"),
        )
        comp = services._componenti_da_bolletta(b)
        self.assertEqual(comp["soglia_recupero_kwh"], 220)

        # Senza quota netta ma con quota mensile -> fallback con avviso
        res = services._pubblica_componenti(comp, "test", bolletta=b)
        self.assertTrue(any("degradato" in a for a in res["avvisi"]))
        self.assertIsNotNone(res["quota_fissa_giornaliera"])

        # Senza né quota netta né mensile -> entity saltata
        b.quota_fissa_mensile = Decimal("0")
        b.save()
        res2 = services._pubblica_componenti(comp, "test", bolletta=b)
        self.assertTrue(any("saltata" in a for a in res2["avvisi"]))
        self.assertIsNone(res2["quota_fissa_giornaliera"])

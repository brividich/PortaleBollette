"""
Comando Django per importare in blocco tutte le bollette PDF presenti in una cartella locale.
Uso:
    python manage.py importa_cartella "C:\\percorso\\alle\\bollette"
    python manage.py importa_cartella "C:\\percorso\\alle\\bollette" --sovrascrivi
"""
import os
from django.core.management.base import BaseCommand
from bollette import parser_service


class Command(BaseCommand):
    help = "Importa massivamente tutti i file PDF di bollette da una cartella del computer."

    def add_arguments(self, parser):
        parser.add_argument(
            "cartella",
            type=str,
            help="Percorso assoluto o relativo della cartella contenente i PDF.",
        )
        parser.add_argument(
            "--sovrascrivi",
            action="store_true",
            help="Sovrascrive le bollette se già presenti per lo stesso periodo.",
        )
        parser.add_argument(
            "--ricorsivo",
            action="store_true",
            default=True,
            help="Cerca i file PDF anche nelle sottocartelle (default: True).",
        )

    def handle(self, *args, **options):
        cartella = options["cartella"]
        sovrascrivi = options["sovrascrivi"]
        ricorsivo = options["ricorsivo"]

        if not os.path.exists(cartella) or not os.path.isdir(cartella):
            self.stderr.write(f"Errore: la cartella '{cartella}' non esiste o non e valida.")
            return

        # Trova tutti i file PDF
        file_pdf = []
        if ricorsivo:
            for root, _, files in os.walk(cartella):
                for f in files:
                    if f.lower().endswith(".pdf"):
                        file_pdf.append(os.path.join(root, f))
        else:
            for f in os.listdir(cartella):
                if f.lower().endswith(".pdf"):
                    file_pdf.append(os.path.join(cartella, f))

        if not file_pdf:
            self.stdout.write(f"Nessun file PDF trovato in '{cartella}'.")
            return

        self.stdout.write(f"Trovati {len(file_pdf)} file PDF. Avvio elaborazione massiva...\n")

        tot_creati = 0
        tot_aggiornati = 0
        tot_saltati = 0
        tot_errori = 0

        for idx, percorso in enumerate(file_pdf, start=1):
            nome_file = os.path.basename(percorso)
            self.stdout.write(f"[{idx}/{len(file_pdf)}] Elaboro: {nome_file} ...", ending=" ")

            try:
                with open(percorso, "rb") as fh:
                    esito = parser_service.importa_file_singolo(fh, nome_file, sovrascrivi=sovrascrivi)

                if not esito.get("ok"):
                    tot_errori += 1
                    self.stdout.write(self.style.ERROR(f"ERRORE ({esito.get('errore')})"))
                elif esito.get("stato") == "creato":
                    tot_creati += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"OK CREATA [{esito['tipo'].upper()}] {esito['fornitore']} - "
                            f"{esito['periodo']} ({esito['consumo']}, {esito['importo']})"
                        )
                    )
                elif esito.get("stato") == "aggiornato":
                    tot_aggiornati += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"AGGIORNATA [{esito['tipo'].upper()}] {esito['fornitore']} - {esito['periodo']}"
                        )
                    )
                elif esito.get("stato") == "saltato":
                    tot_saltati += 1
                    self.stdout.write(f"GIA PRESENTE (Saltata)")

            except Exception as exc:
                tot_errori += 1
                self.stdout.write(self.style.ERROR(f"ECCEZIONE ({exc})"))

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(
            self.style.SUCCESS(
                f"IMPORTAZIONE COMPLETATA:\n"
                f"  - Totale file: {len(file_pdf)}\n"
                f"  - Nuove create: {tot_creati}\n"
                f"  - Aggiornate: {tot_aggiornati}\n"
                f"  - Gia presenti: {tot_saltati}\n"
                f"  - Errori: {tot_errori}"
            )
        )

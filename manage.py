#!/usr/bin/env python
"""Utility da riga di comando di Django per il tool di analisi bollette."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django non risulta installato. Attiva il virtualenv e installa "
            "le dipendenze con 'pip install -r requirements.txt'."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

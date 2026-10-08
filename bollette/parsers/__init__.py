"""
Modulo Parser Multifornitore per il Portale Bollette.
Contiene l'architettura a Strategy Pattern per l'ingestion specializzata di bollette italiane.
"""
from .base import BaseProviderParser
from .registry import MultiFornitoreRegistry, get_parser_registry

__all__ = ["BaseProviderParser", "MultiFornitoreRegistry", "get_parser_registry"]

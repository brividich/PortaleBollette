# ⚡ Portale Bollette — Smart Energy & Home Assistant Hub

Portale web open-source in Python/Django per la **gestione intelligente delle bollette energetiche (Luce e Gas)**, il **calcolo del prezzo marginale reale a scaglioni fiscali**, l'integrazione bidirezionale con **Home Assistant** e la correlazione climatica avanzata (**Open-Meteo & Netatmo**).

---

## 🌟 Funzionalità Principali

### 1. 📄 Parser Multi-Fornitore Deterministico (14+ Brand & ARERA)
- **Architettura Strategy Pattern**: parser dedicati per *Enel Energia, Servizio Elettrico Nazionale (SEN), Eni Plenitude, Acea Energia, A2A, Octopus Energy, Edison, Hera Comm, Iren, Sorgenia, NeN, Dolomiti Energia, E.ON, Poste Italiane* oltre a un fallback conforme alla Bolletta 2.0 ARERA.
- **Estrazione Automatica**: lettura da PDF di POD, PDR, date di competenza, consumi disaggregati mensili (kWh / Smc), quote fisse di periodo dallo "Scontrino dell'energia", imposte e coefficienti volumetrici (C e PCS).
- **Tolleranza ARERA**: supporto sia per la dicitura *Componente energia + dispacciamento* (pre-maggio 2026) sia per *Corrispettivo per il consumo + CDISPD* (delibera ARERA 386/2025).
- **Batch Upload**: importazione massiva di decine di bollette in contemporanea con protezione da duplicati.

### 2. 💰 Modello Fiscale Accisa a Tre Tratti (ADR-006)
- **Modello Fiscale Elettrico Reale**: l'accisa erariale per utenze domestiche residenti non ha un unico scalino ma **tre tratti mensili**:
  - **F = Franchigia**: 150 kWh/mese (accisa esente).
  - **T = Soglia recupero**: 220 kWh/mese.
  - **A = Aliquota ordinaria**: 0,0227 €/kWh (IVA 10% esclusa).
  - Righe fiscali mensili:
    - $\text{Accisa} = \text{round2}(\max(0, k - F) \times A)$
    - $\text{Recupero} = \text{round2}(\min(F, \max(0, k - T)) \times A)$
    - $\text{kWh Tassabili}(k) = \max(0, k - F) + \min(F, \max(0, k - T))$
  - Pendenza marginale del costo variabile:
    - $k \le 150 \implies 0$ (nessuna accisa)
    - $150 < k \le 220 \implies 1$ (accisa ordinaria)
    - $220 < k < 370 \implies 2$ (accisa ordinaria + recupero della franchigia)
    - $k \ge 370 \implies 1$ (franchigia totalmente recuperata)
  > ⚠️ **Nota / Da verificare:** il cap teorico $\min(F, \dots)$ a 370 kWh azzera la franchigia. Nelle bollette storiche analizzate (consumi fino a 335 kWh/mese) il cap non è stato ancora osservato direttamente; è implementato secondo formula normativa ed è da confermare per consumi mensili superiori a 370 kWh.

### 3. 🔌 Integrazione Home Assistant (REST API & Helper)
- **Pubblicazione Componenti Marginali**: invio automatico a Home Assistant di:
  - `input_number.prezzo_kwh_base` (prezzo componente energia IVA incl.)
  - `input_number.accisa_marginale_kwh` (accisa marginale IVA incl.)
  - `input_number.soglia_accisa_kwh` (150 kWh)
  - `input_number.soglia_recupero_kwh` (220 kWh)
  - `input_number.quota_fissa_giornaliera` (calcolata su $\text{quota fissa netta di periodo} \times 1,10 / \text{giorni}$)
  - `sensor.portale_bollette_stato` (diagnostica e tracciamento sync)
- **Template Sensor Runtime**: configurazione template pronta in `docs/ha_prezzo_scaglioni.yaml` per calcolare in tempo reale il costo del prossimo kWh consumato.
- **Controllo Dispositivi & Smart Plug**: telemetria live e gestione relè/termostati.

### 4. 🌍 Clima & Modello Termico PRISM
- **Open-Meteo API**: scaricamento automatico della temperatura media e Gradi Giorno (Heating Degree Days) del periodo di fatturazione.
- **Benchmark di Borsa**: spread calcolato automaticamente rispetto a PUN (Luce) e PSV (Gas).

### 5. 🔥 Monitoraggio Netatmo Riscaldamento
- Calcolo delle ore di fiamma attiva della caldaia e del rendimento orario di combustione (Smc/h ed €/h).

---

## 🚀 Installazione Rapida

### 1. Clona il repository
```bash
git clone https://github.com/brividich/PortaleBollette.git
cd PortaleBollette
```

### 2. Crea e attiva l'ambiente virtuale
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Installa le dipendenze
```bash
pip install -r requirements.txt
```

### 4. Configurazione (.env)
Copia il file `.env.example` in `.env`:
```bash
# Windows
copy .env.example .env

# Linux / macOS
cp .env.example .env
```
Configura l'URL di Home Assistant (es. `https://homeassistant.local:8123`) e il `HA_TOKEN`.

### 5. Esegui le migrazioni del database
```bash
python manage.py migrate
```

### 6. Avvia il server di sviluppo
```bash
python manage.py runserver 8001
```
Il portale sarà accessibile su: **http://127.0.0.1:8001/**

---

## 🧪 Esecuzione Test Suite

La suite di test automatizzati copre tutti i modelli matematici fiscali, i parser e i bridge:

```bash
python manage.py test bollette
```

---

## 📦 Struttura del Progetto

```
PortaleBollette/
├── bollette/
│   ├── fiscalita.py          # Modello puro accisa a 3 tratti, ripartizione e quote
│   ├── parsers/              # Architettura Strategy Multi-Fornitore
│   │   ├── base.py           # Classe base e utility di estrazione regex
│   │   ├── providers.py      # Parser specializzati (Acea, Enel, Plenitude, ecc.)
│   │   └── registry.py       # Dispatcher automatico e confidence scoring
│   ├── tests/                # Suite di test modulare
│   │   ├── test_fiscalita.py # Test matematici modello a 3 tratti e bollette golden
│   │   ├── test_parser_acea.py # Test scontrino, letture e box offerta
│   │   ├── test_services_fiscalita.py # Test servizi marginali e HA bridge
│   │   └── test_legacy.py    # Test di regressione esistenti
│   ├── templates/bollette/   # Interfaccia utente web
│   ├── models.py             # BollettaElettrica, BollettaGas, Configurazione
│   ├── services.py           # Logica di dominio disaccoppiata (calcolo puro vs I/O)
│   ├── ha_client.py          # Bridge REST Home Assistant nativo fail-safe
│   └── views.py              # Controller e API endpoints
├── config/                   # Impostazioni Django e routing URL
├── docs/                     # Documentazione e template Home Assistant
│   └── ha_prezzo_scaglioni.yaml
├── ADR_analisi_bollette.md   # Architectural Decision Records (ADR-001..ADR-006)
├── requirements.txt
├── LICENSE                   # Licenza open-source MIT
└── manage.py
```

---

## 🛡️ Sicurezza e Autenticazione Opzionale

- **Priorità Variabili d'Ambiente**: le credenziali Home Assistant lette da variabili d'ambiente (`HA_BASE_URL`, `HA_TOKEN`) hanno priorità assoluta su quelle memorizzate nel database. I token non vengono mai inseriti nei log applicativi.
- **Middleware `PORTALE_REQUIRE_LOGIN`**: per ambienti esposti in rete locale condivisa o su tunnel, è possibile imporre l'autenticazione obbligatoria per tutte le schermate e API impostando in `.env`:
  ```env
  PORTALE_REQUIRE_LOGIN=True
  ```
  Se abilitato, qualsiasi richiesta non autenticata viene reindirizzata alla pagina di login.

---

## 📄 Licenza
Rilasciato sotto licenza [MIT](LICENSE). Copyright (c) 2026 brividich.

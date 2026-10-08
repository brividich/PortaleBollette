# ⚡ Portale Bollette — Smart Energy & Home Assistant Hub

Portale web open-source in Python/Django per la **gestione intelligente delle bollette energetiche (Luce e Gas)**, il **calcolo del prezzo marginale reale**, l'integrazione bidirezionale con **Home Assistant** e la correlazione climatica avanzata (**Open-Meteo & Netatmo**).

---

## 🌟 Funzionalità Principali

### 1. 📄 Parser Multi-Fornitore Deterministico (14+ Brand & ARERA)
- **Architettura Strategy Pattern**: parser dedicati per *Enel Energia, Servizio Elettrico Nazionale (SEN), Eni Plenitude, Acea Energia, A2A, Octopus Energy, Edison, Hera Comm, Iren, Sorgenia, NeN, Dolomiti Energia, E.ON, Poste Italiane* oltre a un fallback conforme alla Bolletta 2.0 ARERA.
- **Estrazione Automatica**: lettura da PDF di POD, PDR, date di competenza, consumi (kWh / Smc), importi, quote fisse, accise e coefficienti volumetrici (C e PCS).
- **Batch Upload**: importazione massiva di decine di bollette in contemporanea con protezione da duplicati.

### 2. 💰 Calcolo Tariffa Marginale Reale (ADR-002)
- Disaggregazione dei costi fissi (CCV/PCV/commercializzazione) rispetto alla sola quota variabile energetica.
- Gestione automatica dello scaglione accisa residenziale (sotto/sopra 150 kWh/mese).
- Pubblicazione automatica del valore marginale su Home Assistant (`input_number.prezzo_energia_kwh` e `input_number.prezzo_gas_smc`) per alimentare il dashboard *Energia*.

### 3. 🔌 Integrazione Home Assistant (REST API & Webhook)
- **Live Metering**: telemetria istantanea della potenza della casa e carichi per elettrodomestico (Shelly EM, Shelly Plus 1PM, Sonoff, Tuya).
- **Controllo Smart Plug**: accensione e spegnimento carichi direttamente dall'interfaccia.
- **Audit & Congruenza**: confronto dei kWh fatturati con le misurazioni reali delle pinze amperometriche, identificando discrepanze e anomalie nei consumi.

### 4. 🌍 Clima & Modello Termico PRISM
- **Open-Meteo API**: scaricamento automatico della temperatura media e Gradi Giorno (Heating Degree Days) del periodo di fatturazione.
- **Benchmark di Borsa**: spread calcolato automaticamente rispetto a PUN (Luce) e PSV (Gas).
- **Modello PRISM**: correlazione lineare tra fabbisogno termico, isolamento dell'involucro edilizio e consumo per grado giorno.

### 5. 🔥 Monitoraggio Netatmo Riscaldamento
- Importazione file di utilizzo da Netatmo Energy WebApp o sincronizzazione via API / Home Assistant.
- Calcolo delle ore di fiamma attiva della caldaia e del rendimento orario di combustione (Smc/h ed €/h).

---

## 🚀 Installazione Rapida

### 1. Clona il repository
```bash
git clone https://github.com/TUO-USERNAME/portale-bollette.git
cd portale-bollette
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

### 4. Configurazione (.env opzionale)
Copia il file di esempio per configurare il token di Home Assistant o le coordinate geografiche (tutti i parametri sono configurabili anche direttamente dall'interfaccia web in *Centro Controllo*):
```bash
copy .env.example .env
```

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

La suite di test automatizzati copre tutti i parser, la validazione delle date e le comunicazioni API:

```bash
python manage.py test bollette
```

---

## 📦 Struttura del Progetto

```
portale-bollette/
├── bollette/
│   ├── parsers/              # Architettura Strategy Multi-Fornitore
│   │   ├── base.py           # Classe base e utility di estrazione regex
│   │   ├── providers.py      # 14 parser specifici per fornitore
│   │   └── registry.py       # Dispatcher automatico e confidence scoring
│   ├── templates/bollette/   # Interfaccia grafica glassmorphism
│   │   ├── base.html         # App shell con sidebar e topbar
│   │   ├── home.html         # Bento dashboard con grafici e live carichi
│   │   ├── archivio.html     # Archivio con filtri a pillola
│   │   ├── dettaglio_luce.html
│   │   ├── dettaglio_gas.html
│   │   ├── dispositivi_ha.html
│   │   ├── confronto_ha.html
│   │   ├── netatmo.html
│   │   ├── statistiche.html
│   │   └── configurazioni.html
│   ├── models.py             # BollettaElettrica, BollettaGas, Configurazione
│   ├── services.py           # Calcolo tariffe marginali e medie mobili
│   ├── ha_client.py          # Bridge REST Home Assistant
│   └── views.py              # Controller e API endpoints
├── config/                   # Impostazioni Django e routing URL
├── ADR_analisi_bollette.md   # Architectural Decision Records
├── requirements.txt
└── manage.py
```

---

## 🛡️ Licenza & Privacy
- **100% Locale & Privato**: il database SQLite e i file PDF risiedono esclusivamente sulla tua macchina. Nessun dato viene inviato a server esterni (salvo le chiamate verso la tua istanza locale di Home Assistant e le API meteo pubbliche Open-Meteo).
- Licenza open source MIT.

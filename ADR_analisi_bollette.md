# ADR — Tool di analisi bollette gas + luce

> **Architecture Decision Record** del tool personale di analisi bollette
> (Django su Synology Docker + SQLite, parsing PDF via Ollama su PC con RTX 5070 Ti).
>
> Questo file raccoglie le decisioni architetturali in ordine cronologico.
> Ogni ADR è immutabile una volta accettato: le revisioni si fanno con un nuovo ADR
> che supera (`supersedes`) il precedente.

---

## ADR-001 — Architettura base del tool (STUB ricostruito — DA COMPLETARE)

> ⚠️ **Nota:** il file `ADR_analisi_bollette.md` originale non era presente
> nel repo al momento della stesura dell'ADR-002. Questo ADR-001 è uno **stub
> ricostruito dal contesto** che richiede revisione e completamento.

- **Stato:** 📝 STUB (da completare)
- **Contesto:** tool personale always-on per archiviare e analizzare le bollette
  gas + luce, con calcolo dei prezzi marginali variabili.
- **Decisioni note (dal contesto):**
  - **Stack:** Django, eseguito in un container Docker su NAS Synology.
  - **Persistenza:** SQLite (singolo utente, carico basso, backup gestito dal NAS).
  - **Parsing PDF:** delegato a un'istanza **Ollama** sul PC desktop con RTX 5070 Ti
    (il NAS non ha GPU); il tool invia il PDF/testo e riceve i campi strutturati.
  - **Dominio:** bollette monorarie (riferimento fornitore Acea), distinzione tra
    **quote fisse** (commercializzazione, quota potenza, ecc.) e **prezzo marginale
    variabile €/kWh** dell'energia.
- **Da completare:** modello dati delle bollette, pipeline di ingest, gestione
  errori parsing, schema delle entità Django esistenti.

---

## ADR-002 — Bridge Home Assistant (scrittura prezzo + validazione consumi)

- **Stato:** 🟢 **ACCETTATO & IMPLEMENTATO**
- **Data:** 2026-06-29
- **Decisori:** Luca (owner del tool)
- **Si aggancia a:** ADR-001

### Contesto e problema

Oggi il prezzo marginale €/kWh dell'energia elettrica viene aggiornato **a mano**
dentro Home Assistant (HA) ogni volta che arriva una nuova bolletta. Inoltre non
c'è un confronto sistematico tra i **kWh fatturati** in bolletta e i **kWh
realmente misurati** in casa nel medesimo periodo.

Vogliamo un **bridge bidirezionale** tra il tool e HA:

1. **SCRITTURA** — dopo il parsing di una bolletta elettrica, il tool calcola il
   prezzo marginale variabile €/kWh e lo **pubblica in HA** automaticamente.
2. **LETTURA / VALIDAZIONE** — il tool **legge da HA** i kWh misurati nel periodo
   della bolletta e li confronta con i kWh fatturati, misurando lo scostamento %
   e verificando la convergenza *stima ↔ bolletta reale*.

### Vincolo architetturale chiave

Il tool è un **servizio always-on** su Synology. Per questo **NON usa `ha-mcp`**:
`ha-mcp` è la via di Claude Code (sessione interattiva), non di un servizio
headless. Il tool parla con HA esclusivamente via **REST API nativa di Home
Assistant** autenticata con un **Long-Lived Access Token**.

> `ha-mcp` resta in uso **solo lato Claude Code**, per costruire e testare le
> automazioni/template HA (es. il template che applica lo scalino accisa runtime).

---

### (a) Trasporto: REST API HA + token in variabile d'ambiente

- **Endpoint base:** `https://homeassistant.local:8123/api/...`
- **Configurazione (MAI hardcoded):**
  - `HA_BASE_URL` — es. `https://homeassistant.local:8123`
  - `HA_TOKEN` — Long-Lived Access Token (header `Authorization: Bearer <token>`)
- Entrambe lette **solo da variabili d'ambiente** (iniettate nel container Docker
  via `env_file` / secrets), mai nel codice e mai nel repo.

**Endpoint REST usati:**

| Operazione | Metodo / path | Uso |
|---|---|---|
| Scrivere un helper | `POST /api/services/input_number/set_value` con body `{"entity_id": ..., "value": ...}` | Pubblicare il prezzo |
| Leggere uno stato | `GET /api/states/<entity_id>` | Leggere valore corrente di un sensore |
| Storico nel periodo | `GET /api/history/period/<start_iso>?end_time=<end_iso>&filter_entity_id=<id>` | Calcolare i kWh consumati tra inizio e fine periodo bolletta |
| Probe disponibilità | `GET /api/` | Health check prima della sync |

**REST vs WebSocket — decisione: REST.**

- *Pattern d'uso:* la scrittura del prezzo è **sporadica** (≈1 volta/mese, alla
  bolletta) e le letture sono **on-demand** (quando apro la validazione). Non c'è
  alcun bisogno di reagire in tempo reale a cambi di stato di HA.
- *WebSocket* avrebbe senso solo per **subscribe a eventi** / streaming continuo:
  comporterebbe gestire una connessione persistente, reconnect, heartbeat — costo
  e complessità ingiustificati per un servizio che tocca HA poche volte.
- *REST* è **stateless**, semplice (una `requests.post`/`get` con header Bearer),
  facile da rendere fail-safe e da loggare. Per questo caso d'uso è **sufficiente
  e preferibile**.

---

### (b) Cosa scrive il tool in HA  `[DA CONFERMARE]`

Il prezzo marginale non è lineare: c'è **un solo scalino**, lo scatto accisa a
**150 kWh/mese** (uso domestico residente 3 kW). Sotto soglia l'accisa è 0, sopra
si aggiunge l'accisa marginale.

Due opzioni di pubblicazione:

- **Opzione A — 3 helper "componenti"** (fonte di verità, HA calcola lo scalino a runtime):
  - `input_number.prezzo_kwh_base`      → 0.1934  (€/kWh, IVA 10% incl., ≤150 kWh)
  - `input_number.accisa_marginale_kwh` → 0.0250  (€/kWh aggiuntivi oltre soglia)
  - `input_number.soglia_accisa_kwh`    → 150     (kWh/mese)
  - In HA un *template sensor* applica lo scalino in base ai kWh del mese in corso.
- **Opzione B — singolo valore già spalmato:**
  - `input_number.prezzo_energia_kwh`   → media marginale già calcolata dal tool.

**Proposta (DA CONFERMARE): scrivere ENTRAMBI.**
I 3 componenti come **fonte di verità** (così HA resta corretto anche al variare
dei kWh nel mese e replica esattamente la logica della bolletta), e il singolo
`prezzo_energia_kwh` come **comodità/fallback** per dashboard o automazioni che
vogliono un numero unico senza ricalcolare lo scalino.

> ✅ **Conferma richiesta:** scrivo entrambi (A+B), oppure solo A, oppure solo B?
> E confermi i nomi delle entity `input_number.*` qui sopra (o esistono già con
> altri nomi in HA)?

---

### (c) Modalità di calcolo del valore pubblicato  `[DA CONFERMARE]`

Setting `PREZZO_HA_MODE ∈ {ultima_bolletta, media_mobile_12m}`.

- **`ultima_bolletta`** — pubblica il prezzo marginale dell'ultima bolletta parsata.
  - *Pro:* riflette subito l'ultimo costo reale; nessuna dipendenza dallo storico.
  - *Contro:* "scatta" mese su mese, sensibile a una singola bolletta anomala
    (conguagli, mesi con consumi molto diversi → scalino accisa attivo o no).
- **`media_mobile_12m`** — media **pesata sui kWh** delle bollette degli ultimi 12 mesi.
  - *Pro:* stabile, rappresentativo del costo medio annuo, smussa stagionalità e
    l'effetto on/off dello scalino accisa.
  - *Contro:* reagisce con ritardo a una variazione tariffaria reale; richiede
    almeno qualche bolletta in archivio per essere significativo.
  - ⚠️ **media PESATA sui kWh**, non aritmetica secca: ogni bolletta pesa per i
    propri kWh fatturati (mesi più consumati pesano di più). Formula:
    `Σ(prezzo_marginale_medio_i × kwh_i) / Σ(kwh_i)`.

**Default proposto (DA CONFERMARE): `media_mobile_12m`.**

> ✅ **Conferma richiesta:** confermi `media_mobile_12m` come default?
> Fallback: se in archivio c'è < 1 anno di bollette, uso le bollette disponibili
> (e lo segnalo), oppure faccio fallback a `ultima_bolletta`?

---

### (d) Cosa legge il tool da HA per la validazione  `[DA SCOPRIRE — niente assunzioni]`

Servono i **kWh totali casa consumati nel periodo esatto della bolletta**
(le date bolletta **non** coincidono col mese solare, quindi un utility_meter
mensile che resetta al 1° del mese **non basta** da solo).

**Candidati da verificare in HA (NON assunti):**

1. **Sensore cumulativo rete** (`state_class: total_increasing`, `device_class:
   energy`, unità `kWh`) — tipicamente la "Grid consumption" della dashboard
   Energia. È l'ideale: si calcola il **delta** `valore(fine) − valore(inizio)`
   via `/api/history/period`. ← *candidato preferito*.
2. **`utility_meter`** con ciclo mensile (es. `sensor.energia_casa_mensile`):
   utile come riscontro, ma allineato al mese solare, non al periodo bolletta.
3. **Sensore del contatore / inverter / Shelly EM / P1 meter** che espone l'energia
   importata totale.
4. **Sensore custom della dashboard Energia** già configurato per i consumi rete.

**Come li scopro davvero (quando avrò token/accesso):**
`GET /api/states` → filtro le entity con `attributes.unit_of_measurement == "kWh"`
e `attributes.device_class == "energy"`, prediligendo `state_class: total_increasing`.
Ti porto la lista dei candidati reali e **scegli tu** l'entity da usare per la
validazione (la metto poi in un setting `HA_ENTITY_KWH_CONSUMO`).

> ✅ **Conferma richiesta:** vuoi che proceda io a scoprire le entity via REST
> (mi serve il token), o me le indichi tu direttamente?

---

### (e) Fail-safe: gli errori HA non bloccano MAI il flusso bolletta

**Principio:** il parsing e l'**archiviazione** della bolletta devono **sempre**
completare con successo, indipendentemente dallo stato di HA. HA è un *sink*
secondario, non parte della transazione della bolletta.

**Strategia:**

1. **Disaccoppiamento transazionale:** la bolletta viene salvata e committata
   nella sua transazione. La sync HA avviene **dopo il commit**, in un blocco
   `try/except` che **non rilancia** nel flusso bolletta.
2. **Coda persistente:** ogni tentativo di sync scrive una riga in **`HaSyncLog`**
   (vedi FASE 2) con esito. Una sync fallita lascia la bolletta in stato
   `ha_sync_ok = False` → è "in coda" per il ritento.
3. **Retry esponenziale entro il singolo tentativo:** `requests` con `timeout`
   esplicito (es. connect 3s / read 5s) e backoff `2s → 4s → 8s` (max 3 tentativi).
   Se tutti falliscono, si **abbandona senza eccezioni** e si lascia in coda.
4. **Ripresa della coda:** le bollette con `ha_sync_ok = False` vengono ritentate
   (a) al prossimo ingest, (b) col bottone manuale "Pubblica prezzo su HA", e/o
   (c) opzionalmente da un job periodico (es. cron/management command). `[DA CONFERMARE]`
   se vuoi anche il job periodico o solo retry manuale + al-prossimo-ingest.
5. **Idempotenza:** ripubblicare lo stesso prezzo è sicuro (set_value è idempotente).

---

### (f) Sicurezza

- **Token e URL solo da env** (`HA_TOKEN`, `HA_BASE_URL`) — mai hardcoded, mai nel repo.
- **TLS verificato di default.** HA su `homeassistant.local:8123` usa spesso un
  **certificato self-signed** o Let's Encrypt interno → introduco:
  - `HA_TLS_VERIFY` (default `True`),
  - `HA_CA_BUNDLE` (path a un CA bundle/cert custom per validare il self-signed).
  - Disattivare la verifica TLS è possibile **solo** mettendo esplicitamente
    `HA_TLS_VERIFY=False`, che logga un **warning** una tantum. Mai default silenzioso.
- **Nessun segreto nei log:** il token non viene mai loggato; in `HaSyncLog` e nei
  log applicativi si registrano endpoint, entity, valore, esito, codice HTTP — **mai
  l'header Authorization**. Eventuale dump della request va mascherato (`Bearer ***`).

---

### Conseguenze

- **Positive:** niente più aggiornamento manuale del prezzo in HA; validazione
  automatica fatturato↔misurato; HA replica fedelmente lo scalino accisa; il flusso
  bolletta è robusto a HA offline.
- **Negative / costi:** dipendenza operativa da rete/HA per la sync (mitigata da
  coda + retry); necessità di gestire il self-signed TLS; un template sensor lato HA
  da mantenere (se si sceglie l'opzione A dei 3 componenti).

---

### ✅ Punti da confermare prima della FASE 2 (codice)

1. **(b)** Scrivo **A+B** (3 componenti + singolo), o solo A, o solo B? Nomi entity OK? -> Implementato A+B.
2. **(c)** Default `media_mobile_12m` confermato? Comportamento con < 12 mesi di storico? -> Confermato con fallback a ultima_bolletta.
3. **(d)** Scopro io le entity kWh via REST (serve token) o me le indichi tu? -> Introdotto wizard di Discovery via REST API nativa.
4. **(e)** Voglio anche il **job periodico** di ripresa coda, o basta manuale + al-prossimo-ingest? -> Implementato `sync_ha.py`.
5. **(f)** Confermi gestione self-signed via `HA_CA_BUNDLE` (preferito) vs `HA_TLS_VERIFY=False`? -> Implementato con supporto a entrambi.

---

## ADR-003 — Estensione Gas, API Esterne (Open-Meteo & Benchmark Mercato) e Ingest PDF

- **Stato:** 🟢 **ACCETTATO & IMPLEMENTATO**
- **Data:** 2026-09-26
- **Decisori:** Luca (owner del tool)
- **Si aggancia a:** ADR-001, ADR-002

### Contesto e Decisioni

1. **Modulo Gas Naturale (`BollettaGas`):**
   - Aggiunto modello dedicato per il gas con tracciamento di Smc fatturati, coefficiente correttivo $C$, potere calorifico superiore (PCS), materia prima ed accise.
   - Calcolo del prezzo marginale medio €/Smc e pubblicazione in Home Assistant su `input_number.prezzo_gas_smc`.
2. **Integrazione Open-Meteo API (Normalizzazione Climatica):**
   - Interrogazione automatica dell'API open-source Open-Meteo per il periodo di fatturazione [inizio, fine].
   - Calcolo dei Gradi Giorno di Riscaldamento (HDD base 18°C) e temperatura media per calcolare gli indici di efficienza $\text{kWh} / \text{HDD}$ e $\text{Smc} / \text{HDD}$.
3. **Benchmark di Mercato (PUN e PSV):**
   - Confronto automatico tra prezzo marginale della bolletta e prezzo medio di borsa (PUN per la luce, PSV per il gas).
   - Calcolo dello **Spread fornitore** per verificare la trasparenza e identificare rincari contrattuali.
4. **Pipeline Ingest PDF con Fallback:**
   - Estrazione testo nativa via `pypdf`.
   - Parser euristico per i principali gestori italiani (Acea, Enel, Plenitude, A2A, Octopus, ecc.).
   - Integrazione modulare con Ollama locale (PC con RTX 5070 Ti) e supporto cloud.
5. **Auto-Discovery Sensori Home Assistant:**
   - Endpoint e modal UI dedicati che interrogano `GET /api/states` di Home Assistant e presentano tutti i sensori energetici (Shelly, contatori, inverter) con un clic.

---

## ADR-004 — Archivio Documentale PDF, Configurazione Dinamica e Modulo Statistiche & Confronti

- **Stato:** 🟢 **ACCETTATO & IMPLEMENTATO**
- **Data:** 2026-09-26
- **Decisori:** Luca (owner del tool)
- **Si aggancia a:** ADR-001, ADR-002, ADR-003

### Contesto e Decisioni

1. **Archivio Documentale Completo (`/archivio/`):**
   - Aggiunto il campo `file_bolletta` (`FileField`) sui modelli `BollettaElettrica` e `BollettaGas` con storage in `media/bollette_pdf/`.
   - Permette di conservare permanentemente il PDF originale di ogni fattura, con link di download immediato.
   - Creata la pagina Archivio con filtri multi-parametro (Tipo fornitura, Anno solare, Fornitore), barra di ricerca e pulsante di esportazione CSV/Excel (`/esporta/csv/`).
2. **Schede di Dettaglio Bolletta (`/bolletta/<id>/` e `/bolletta/gas/<id>/`):**
   - Vista approfondita per ogni singola fattura:
     - Calcolo del **Costo Unitario Reale "Tutto Compreso"** (€/kWh o €/Smc effettivo) confrontato con il costo marginale puro.
     - Consumo medio giornaliero e spesa media giornaliera.
     - Scomposizione analitica dei costi (quota fissa, quota variabile, accise).
     - Widget meteo Open-Meteo, widget benchmark di borsa e widget validazione Home Assistant.
3. **Modello e Interfaccia Configurazioni Dinamiche (`ConfigurazioneSistema` & `/configurazioni/`):**
   - Modello singleton per memorizzare e modificare a runtime i parametri del Bridge HA (URL, Token, modalità calcolo, entity monitorate), coordinate geografiche Open-Meteo ed endpoint Ollama.
   - I servizi (`ha_client`, `meteo_client`, `services`) leggono in tempo reale dal database con fallback a `settings.py` / `.env`.
   - Strumenti di diagnostica live con test connettività AJAX per Home Assistant, Open-Meteo e server Ollama.
   - Pannello Django Admin arricchito con sezioni tematiche (`fieldsets`), badge colorati e link al PDF.
4. **Modulo Statistiche & Confronti (`/statistiche/`):**
   - Tabella storica di bilancio energetico aggregata per anno solare.
   - Simulatore tariffario: stima della spesa con offerte fisse vs variabile reale per valutare la convenienza del mercato.
   - Analisi dell'incidenza delle quote fisse annue (PCV/CCV/QVD).

---

## ADR-005 — Integrazione File Utilizzo Netatmo (Termostato Smart) & Analisi Rendimento Caldaia

- **Stato:** 🟢 **ACCETTATO & IMPLEMENTATO**
- **Data:** 2026-10-06
- **Decisori:** Luca (owner del tool)
- **Si aggancia a:** ADR-001, ADR-003, ADR-004

### Contesto e Decisioni

1. **Problema delle Bollette Gas:**
   - A differenza dell'elettricità (che dispone di contatore o pinza Shelly in tempo reale), il gas naturale spesso non dispone di telemetria oraria o giornaliera nel contatore domestico.
   - Il termostato smart (Netatmo Smart Thermostat) controlla l'accensione della caldaia e registra la temperatura interna, il setpoint e la durata effettiva della fiamma (`BoilerOn`).

2. **Decisione Architetturale:**
   - Integrazione diretta nel Portale Bollette senza dipendenza obbligata da Home Assistant:
     - **Caricatore CSV Flessibile (`/netatmo/`):** Supporta le esportazioni della WebApp Netatmo Energy (`Settings > Data management > Download`) con auto-detection di delimitatori (`,`, `;`, `\t`), formati data (timestamp Unix, ISO, GG/MM/AAAA) e intestazioni (`BoilerOn`, `Temperature`, `Sp-Temperature`).
     - **Modello Dati Giornaliero (`NetatmoRecordGiornaliero`):** Aggregazione a livello giornaliero (secondi di fiamma caldaia, temperatura ambiente media, setpoint medio, campionamenti).
     - **Correlazione Automatica con Bollette Gas (`BollettaGas`):**
       - Calcolo delle **ore totali di caldaia** nel periodo di fatturazione.
       - Calcolo del **Rendimento del Bruciatore**: $\text{Smc} / \text{ora caldaia}$.
       - Calcolo del **Costo Orario del Riscaldamento**: $\text{€} / \text{ora caldaia}$.
       - **Scomposizione Analitica dei Consumi:** Riscaldamento ambiente vs Acqua Calda Sanitaria (ACS) e cottura, basato sul baseload estivo.
     - **Cruscotto Dedicato (`/netatmo/`):** KPI aggregati, guida all'esportazione, grafico Chart.js a doppio asse (barre ore caldaia + linee temperatura interna e target), bilancio mensile e correlazione tabellare con tutte le fatture gas.
     - **Arricchimento Schede di Dettaglio Gas (`/bolletta/gas/<id>/`):** Box speciale con le metriche calcolate di fiamma, rendimento e scomposizione.

---

## ADR-006 — Modello Accisa a Tre Tratti, Ripartizione Mensile e Quota Fissa Giornaliera

- **Stato:** 🟢 **ACCETTATO & IMPLEMENTATO**
- **Data:** 2026-10-08
- **Decisori:** Luca, AI Engineer
- **Si aggancia a:** ADR-001, ADR-002, ADR-004

### 1. Contesto e Problema
Il calcolo del prezzo elettrico (€/kWh) e della quota fissa presentava tre discrepanze rispetto alle bollette reali dell'energia elettrica (validato su fatture Acea Energia domestiche residenti):
1. **Accisa e recupero imposta erariale:** L'accisa non segue un singolo scaglione lineare, ma un modello a tre tratti con franchigia ($F = 150\text{ kWh/mese}$), soglia di recupero progressivo ($T = 220\text{ kWh/mese}$), e azzeramento teorico della franchigia a $T + F = 370\text{ kWh/mese}$. L'aliquota base per uso domestico è $A = 0{,}0227\text{ €/kWh}$ (+ IVA 10%).
2. **Soglie mensili su bollette bimestrali:** `calcola_prezzo_marginale` applicava le soglie all'intero volume fatturato della bolletta (tipicamente 2 mesi), sottostimando le accise e il prezzo variabile unitario.
3. **Quota fissa giornaliera errata:** Veniva calcolata come $\text{quota mensile} / 30$ con fallback silenzioso a `Decimal("10.00")`, anziché derivare dalla somma esatta delle quote fisse nette di periodo (vendita + rete + potenza) divise per i giorni effettivi di fatturazione.
4. **Accoppiamento I/O e calcolo:** Il ricalcolo dei prezzi era legato alle chiamate di rete esterne (Open-Meteo, PUN, Netatmo) con blocchi `except Exception: pass` che nascondevano errori.

### 2. Decisioni Architetturali
1. **Modulo puro di fiscalità (`bollette/fiscalita.py`):**
   - Funzioni matematiche pure prive di I/O o accessi ORM: `kwh_tassabili`, `imposte_mese`, `pendenza_accisa`, `prezzo_marginale_al_consumo`, `totale_bolletta_luce`, `quota_fissa_giornaliera`, `ripartisci_consumi_per_mese`.
   - Pendenza marginale accisa determinata analiticamente:
     - $k \le F \implies 0$
     - $F < k \le T \implies 1$
     - $T < k < T+F \implies 2$
     - $k \ge T+F \implies 1$
2. **Ripartizione consumi e persistenza:**
   - Aggiunto `consumi_mensili` (`JSONField`) su `BollettaElettrica` per memorizzare i consumi solari effettivi/stimati estratti dal parser.
   - Parser Acea aggiornato per estrarre le letture e consumi mensili effettivi/stimati (F1..F6) escludendo le righe di riepilogo `Fatturato`.
   - Fallback a pro-rata solare con resto sull'ultimo mese e marcatura `stimato=True` + warning quando il parser non rileva consumi divisi.
3. **Nuova semantica del prezzo marginale medio:**
   - Il prezzo marginale medio della bolletta è il costo variabile medio IVA inclusa ponderato sui tratti mensili:
     $$\text{Costo Variabile Medio} = \frac{\sum_{\text{mese}} (\text{base} \times k + \text{accisa} \times \text{kwh\_tassabili}(k))}{\sum k}$$
4. **Quota fissa reale e trasparenza:**
   - Calcolata come $\text{quota\_fissa\_netta\_periodo} \times (1 + \text{IVA}) / \text{giorni\_periodo}$.
   - Se manca `quota_fissa_netta_periodo`, fallback a `quota_fissa_mensile * 12 / 365 * (1 + IVA)` con avviso esplicito restituito nei dizionari; se manca anche quello, l'entity non viene pubblicata.
5. **Disaccoppiamento del servizio:**
   - Separata `ricalcola_prezzi(bolletta)` (pura memoria/DB) da `arricchisci_con_dati_esterni(bolletta)` (I/O protetto con timeout e logging).
6. **Integrazione Home Assistant:**
   - Pubblicazione di `input_number.soglia_recupero_kwh` (220 kWh/mese).
   - Template sensor `docs/ha_prezzo_scaglioni.yaml` che calcola dinamicamente in tempo reale il prezzo del prossimo kWh in base ai kWh cumulati nel mese corrente.

### 3. Assunzioni e Punti da Verificare
- **Cap del recupero a 370 kWh ($T + F$):** Non osservato empiricamente nelle bollette storiche (consumi massimi registrati 335 kWh/mese). Implementato rigorosamente secondo formula normativa; da monitorare in caso di bollette estive con consumi > 370 kWh/mese.
- **CDISPD / Componente energia:** Nei mesi intermedi con variazioni tariffarie infra-bimestrali (es. maggio 2026 Delibera ARERA 386/2025), il parser e il modello usano il valore base contrattuale unitario della bolletta mediato.
- **Parser altri fornitori:** I parser diversi da Acea continuano al momento a usare la stima pro-rata solare degradata (`stimato=True`) fino all'aggiornamento dei rispettivi estrattori.

### 4. Conseguenze
- Precisione al centesimo su imposte erariali, IVA, totale bolletta e costo marginale reale.
- Home Assistant riflette accuratamente sia il costo medio di periodo che il costo istantaneo a scaglioni.
- Trasparenza totale su canone RAI (escluso dal costo energia/kWh).


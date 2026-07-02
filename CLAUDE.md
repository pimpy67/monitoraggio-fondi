# CLAUDE.md — Monitoraggio Fondi & ETF

Documento di riferimento tecnico per il progetto. Caricato automaticamente a ogni sessione.

> ⚠️ **REGOLA DI SINCRONIZZAZIONE**: Ogni volta che cambiano i parametri nel codice (YAML, technical_analysis.py, monitor.py), **DEVI aggiornare contemporaneamente** le tabelle in questa documentazione (sezione "PARAMETRI DI RIFERIMENTO - PROFILI E REGOLE"). Le due fonti devono coincidere SEMPRE.

---

## Infrastruttura VPS

- **Provider**: Hostinger VPS — Ubuntu 24.04 LTS
- **IP / hostname**: `76.13.37.133` / `srv1407758.hstgr.cloud`
- **SSH**: `ssh root@76.13.37.133`
- **DNS**: gestito da **Cloudflare** (non Hostinger) — record A proxied
- **Reverse proxy**: Nginx
- **SSL**: Let's Encrypt + Cloudflare Full (strict) — se si usa "Flexible" → ERR_TOO_MANY_REDIRECTS

---

## Sistema Fondi — `fund_monitor_system`

### Percorsi
| Risorsa | Percorso |
|---------|----------|
| Sorgente codice (locale) | `C:\Users\andrea.pavan_allievi\Documents\monitoraggio-fondi\` (Windows scuola) |
| Sorgente codice (Mac casa) | `/Users/user/Documents/CORSO_ITS/APPLICAZIONI _ APP/MONITORAGGIO FONDI/fund_monitor_system/` |
| **Git repo VPS = Deploy dir** | `/root/fund_monitor_system/` → remote `https://github.com/pimpy67/monitoraggio-fondi.git` |
| Container **attivo** | `fund-monitor-app-1` → porta **5000** — progetto Docker: `fund-monitor` |
| Nginx config | `/etc/nginx/sites-enabled/fondi` → proxy a **porta 5000** (non cambiare) |
| Dashboard | `https://fondi.andreapavan.tech` |
| Excel (volume mount) | `/root/fund_monitor_system/fondi_monitoraggio.xlsx` → montato in `/app/fondi_monitoraggio.xlsx` |

### Database PostgreSQL
- Container: `fund-monitor-postgres-1`
- Credenziali: user `fundmonitor`, db `funds`, password in `.env` → `DB_PASSWORD`
- **Tabelle**:
  - `price_history` — storico NAV (isin, date, price, source) — UNIQUE su (isin, date)
  - `l0_tracking` — fondi in recupero L0 (entry_price, entry_date, panic_low)
  - `l1_tracking` — fondi in trend sicuro L1
  - `l1_exit_history` — storico uscite L1 (exit_date, exit_rule, entry_date, pct_gain)
  - `portfolio_entries` — portafoglio personale (isin, entry_date, entry_price, fund_name)

### Deploy — unico script

```bash
# Da qualsiasi PC: pubblica GitHub + rebuild immagine + aggiorna container VPS
./deploy.sh
```

`deploy.sh` fa in sequenza:
1. `git add -A` + `git commit` + `git push` (solo se ci sono modifiche)
2. SSH VPS: `git fetch && git reset --hard origin/main` (sincronizza repo git con GitHub)
3. SSH VPS: `docker compose -p fund-monitor build app` (ricostruisce immagine dal codice aggiornato)
4. SSH VPS: `docker compose -p fund-monitor up -d --force-recreate app` (ricrea container)

> **Prima esecuzione**: il build pip install richiede ~14 minuti. I deploy successivi usano il cache Docker — solo pochi secondi se cambiano solo file `.py`/`.html`.

> **Backup DB**: cron automatico ogni giorno alle 18:30 CEST (lun-ven) → `/root/backups/funds_YYYYMMDD.sql.gz` (ultimi 14 giorni). Per copiare l'ultimo backup in locale:
> ```bash
> scp root@76.13.37.133:/root/backups/funds_latest.sql.gz .
> ```

### Comandi rapidi
```bash
# Log live
ssh root@76.13.37.133 "docker logs fund-monitor-app-1 --tail=30 -f"

# Trigger manuale monitor
ssh root@76.13.37.133 "curl -s -X POST http://localhost:5000/api/trigger-update"

# Query DB
ssh root@76.13.37.133 "docker exec fund-monitor-postgres-1 psql -U fundmonitor -d funds -c '<SQL>'"

# Lista backup disponibili
ssh root@76.13.37.133 "ls -lah /root/backups/"
```

### File principali
| File | Ruolo |
|------|-------|
| `app.py` | Flask API + serving dashboard |
| `monitor.py` | Logica principale: fetch NAV, calcolo livelli, aggiorna Excel, salva DB |
| `technical_analysis.py` | Indicatori tecnici, profili asset, logica L0/L1/L2/L3 |
| `data_fetcher.py` | Fetch NAV da FT Markets (primario) e Yahoo Finance (backup) |
| `database.py` | Wrapper PostgreSQL |
| `scheduler.py` | Job scheduler — run principale 18:00 CEST + run silenzioso 09:00 CEST |
| `dashboard.html` | Frontend SPA (HTML+JS, servito da Flask) |
| `fondi_monitoraggio.xlsx` | Excel con tutti i fondi — fonte di verità per lista e livelli |
| `backfill_historical.py` | Backfill storico da FT Markets (richiede 210gg, ottiene ~20-22gg reali) |

### Variabili d'ambiente `.env`
```
RESEND_API_KEY=...
EMAIL_SENDER=onboarding@resend.dev
EMAIL_RECIPIENT=andreapavan67@gmail.com
DB_PASSWORD=FundMonitor2026!
MONITOR_HOUR=16
MONITOR_MINUTE=0
MONITOR_HOUR_SOFT=7
MONITOR_MINUTE_SOFT=0
MONITOR_DAYS=1-5
RUN_ON_START=false
```

### Fonte dati NAV
- **FT Markets** `markets.ft.com/data/funds/tearsheet/historical?s={ISIN}:EUR` — fonte principale
- **Limite**: FT Markets restituisce solo ~20-22 giorni di storico via scraping HTML (non importa il range date)
- **Yahoo Finance** — usato solo come backup singolo prezzo, non per storico
- I dati si accumulano 1 punto/giorno tramite monitor quotidiano
- Stato storico tipico: la maggior parte dei fondi ha 41-70 giorni (sistemi avviati mar-apr 2026)

---

## Sistema ETF — `etf_monitor_system`

### Percorsi
| Risorsa | Percorso |
|---------|----------|
| Sorgente codice (Mac) | `/Users/user/Documents/CORSO_ITS/APPLICAZIONI _ APP/MONITORAGGIO FONDI/etf_monitor_system/` |
| Sorgente codice (VPS) | `/root/etf_monitor_system/` |
| Container | `etf_monitor_system-app-1` → porta **5001** |
| Dashboard | `https://etf.andreapavan.tech` |
| Nginx config | `/etc/nginx/sites-enabled/etf` |
| Git remote | `origin` → `git@github-pimpy67:pimpy67/etf-monitor-system.git` |

### Comandi rapidi ETF
```bash
# Copia file Mac → VPS
scp "/Users/user/Documents/CORSO_ITS/APPLICAZIONI _ APP/MONITORAGGIO FONDI/etf_monitor_system/<file>" root@76.13.37.133:/root/etf_monitor_system/

# Copia file VPS → container
docker cp /root/etf_monitor_system/<file> etf_monitor_system-app-1:/app/

# Riavvia container (OBBLIGATORIO dopo docker cp su file .py)
docker restart etf_monitor_system-app-1

# Log live
docker logs etf_monitor_system-app-1 --tail=30 -f

# Trigger manuale monitor
curl -X POST http://localhost:5001/api/trigger-update

# Query DB
docker exec etf_monitor_system-postgres-1 psql -U etfmonitor -d etfs -c "<SQL>"

# Git pull VPS (il monitor modifica xlsx e dashboard.html → scartare prima)
cd /root/etf_monitor_system && git checkout -- etf_monitoraggio.xlsx dashboard.html && git pull origin main
```

### Caratteristiche ETF vs Fondi
- ETF usa **Yahoo Finance OHLCV** (ticker formato Yahoo, es. `SWDA.L`, `ENRJ.PA`)
- Indicatori: EMA20, SMA50, SMA200, ADX14, RSI14
- SMA200 come **filtro regime bear market** (se ETF sotto SMA200 → no nuovi ingressi L1)
- **214 ETF analizzati** (aggiornato 22/05/2026 — era 195; aggiunti 19 ETF per colmare gap di copertura)
- Database: PostgreSQL in Docker (user: etfmonitor, db: etfs), tabelle: `etf_price_history`, `etf_l1_tracking`, `etf_l0_tracking`

### Note ticker Yahoo Finance (ETF)
- Molti ETF Amundi precedentemente su `.MI` (Borsa Italiana) non più indicizzati da Yahoo Finance
- Migrati a `.L` (LSE), `.DE`/`.F` (XETRA), `.PA` (Euronext Paris), `.AS` (Amsterdam)
- Per trovare ticker alternativo dato un ISIN: `https://query1.finance.yahoo.com/v1/finance/search?q={ISIN}`
- **ISIN non recuperabile da yfinance** per ETF europei: `t.isin` restituisce `'-'`, OpenFIGI non funziona
- Se ISIN è vuoto in Excel, il monitor usa il Ticker come identificatore di fallback nel DB
- 13 ticker ancora irrisolti (probabili delistati) — vedi ISINs in `memory/project_ticker_issues.md`

### Excel `etf_monitoraggio.xlsx` — colonne (foglio "ETF")
| # | Nome | Tipo | Note |
|---|------|------|------|
| 1 | Livello | int | 0/1/2/3 — aggiornato automaticamente |
| 2 | Ticker | str | Ticker Yahoo Finance (es. SWDA.L) |
| 3 | Nome ETF | str | |
| 4 | Categoria | str | Usata per asset_type |
| 5 | Borsa | str | Londra / Francoforte / Parigi / Milano ecc. |
| 6 | Valuta | str | USD / EUR / GBP |
| 7 | Prezzo | float | Ultimo close |
| 8 | EMA13 | float | Aggiornato dal monitor |
| 9 | SMA50 | float | Aggiornato dal monitor |
| 10 | RSI | float | Aggiornato dal monitor |
| 11 | MACD Hist | float | Aggiornato dal monitor |
| 12 | BB Width | float | Aggiornato dal monitor |
| 13 | Segnale | str | L1/L2/L3/L0 WATCH ecc. |
| 14 | Ultima Modifica | str | Timestamp |
| 15 | ISIN | str | Codice ISIN (può essere vuoto — monitor usa Ticker come fallback) |

---

## Schema Livelli ETF (L0 / L1 / L2 / L3) — Parametri Definitivi (22/05/2026)

> Questi parametri sono la versione definitiva concordata e implementata nel codice. La dashboard deve rispecchiare esattamente questi valori.

### L3 — Universe (monitoraggio passivo)
Tutti gli ETF partono da qui. Nessuna condizione richiesta.

### L2 — Watchlist
- Prezzo sopra EMA20 da ≥ 3 giorni consecutivi
- OPPURE: EMA20 > SMA50 (allineamento parziale)

### L1 — Core Portfolio ("Trend Sicuro") — 6 condizioni TUTTE obbligatorie

| # | Condizione | Logica |
|---|-----------|--------|
| 1 | **Allineamento** | price > EMA20 > SMA50 (+ price > SMA200 se mm200_filter=True per asset class) |
| 2 | **Persistenza** | days_above_EMA20 ≥ 3 AND slope(EMA20) > 0 |
| 3 | **RSI ottimale** | rsi_entry_low ≤ RSI ≤ rsi_entry_high (vedi profili sotto) |
| 4 | **Distanza EMA20** | 0% ≤ dist_EMA20 ≤ dist_max (non troppo esteso) |
| 5 | **ADX** | ADX ≥ adx_entry (forza trend, vedi profili sotto) |
| 6 | **MACD momentum** | macd_h > 0 AND (macd_h > macd_h_prev OR dist_EMA20 < 2.0%) |

> **Condizione 6 (MACD)**: blocca ingressi quando EMA20 è ancora positiva per inerzia ma il momentum è già esaurito. Il secondo ramo (dist < 2%) cattura i buy-the-dip vicini all'EMA20 anche con MACD in leggero plateau.

> **Blocco ingresso L1**: Kill Switch attivo (calo giornaliero ≤ −3%) → ingresso bloccato anche se tutte le 6 condizioni sono vere.

### FONDAMENTA IRRINUNCIABILI (FINALE 02/07/2026)

**Tre fondamenta che bloccano L1 anche se 6/6 condizioni sono vere:**

| Fondamenta | Logica | Azione |
|-----------|--------|--------|
| **Regime BULL** | regime_str deve essere "BULL" (non LATERALE/BEAR) | Se regime ≠ BULL → L2 |
| **Prezzo > SMA50** | price deve stare sopra SMA50 (allineamento assoluto) | Se price < SMA50 → L2 |
| **EMA20 Slope >= 0.2%** ⬇️ | EMA20 deve crescere almeno 0.2% su 10 giorni (trend lento ma REALE) | Se slope < 0.2% → L2 |

> **STRATO 2 — Filtro EMA20 slope (FINAL)**: Esclude trend piatti/artificiali senza eliminare trend lenti. Soglia **+0.2%/10gg** è l'equilibrio perfetto. **Risultato finale: 92 L1 → 10 L1** (92% falsi segnali eliminati). ✅

### Profili parametri FAMIGLIE ETF (v2 — AGGIORNATO 02/07/2026)

**⚠️ FONTE AUTOREVOLE: `config/etf_families.yaml`** — La seguente tabella è sincronizzata al YAML e DEVE essere aggiornata ogni volta che cambiano i parametri nel codice.

> Nota: Questo si sostituisce alla vecchia versione "asset type" (equity_developed, commodity, ecc.) che è deprecated. Le famiglie qui sotto sono la base per la classificazione moderna.

| Famiglia | RSI Low–High | ADX entry | days_ema | min_buy | ema_dist_max | l0_dd % | Note |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|------|
| **equity_sviluppati** | 45–55 | **22** ⬆️ | **5** ⬆️ | **5** ⬆️ | 4.0% | 15 | Stringente: trend FORTE |
| **mercati_emergenti** | 40–52 | 22 | 3 | 6 | 5.0% | 20 | Rigoroso: 6/6 obbligatorio |
| **settoriali_growth** | 48–58 | 25 | **5** ⬆️ | **5** ⬆️ | 5.0% | 18 | Tech, AI: persistenza 5gg |
| **settoriali_difensivi** | 42–50 | **18** ⬆️ | 5 | **5** ⬆️ | 2.5% | 15 | Salute, Utility: moderato |
| **bond_governativi** | 38–48 | 12 | 3 | **5** ⬆️ | 1.5% | 8 | No ADX obbligatorio |
| **bond_corp_hy_em** | 42–52 | 15 | 3 | **5** ⬆️ | 2.0% | 10 | Corp/HY: RSI stretto |
| **commodities** | 40–55 | 22 | 3 | 6 | 3.0% | 20 | Oro, Metalli: 6/6 rigoroso |
| **oro_metalli_preziosi** | 38–52 | 18 | 3 | **5** ⬆️ | 2.5% | 15 | PM: volatilità moderata |
| **metalli_industriali** | 38–50 | 20 | 3 | **5** ⬆️ | 3.0% | 18 | Battery, Rame, Zinco |
| **real_estate_reit** | 42–52 | 15 | 3 | **5** ⬆️ | 2.0% | 12 | REIT: no SMA200 |
| **crypto_digital_assets** | 35–52 | 28 | 3 | **5** ⬆️ | 6.0% | 25 | Bitcoin, ETH: volatilità alta |
| **leva_single_stock** | 45–58 | 28 | 3 | **5** ⬆️ | 4.0% | 20 | 3x Long/Short: hold_max=30gg |
| **private_equity_buffer** | 40–55 | 15 | 3 | **5** ⬆️ | 2.5% | 15 | Listed PE: conservativo |
| **monetario_liquidita** | n/a | n/a | 3 | 6 | 0.5% | n/a | XEON: no ADX/RSI |

**Legenda modifiche recenti (02/07/2026)**:
- ⬆️ = Alzato oggi per stringere i criteri L1
  - `min_buy`: 4 → **5** per 8 famiglie (bond, metalli, crypto, PE, REIT, leva) — impedisce ai 4/6 di entrare in L1
  - `adx_entry`: aumentato per equity (18→22, 15→18) — richiede trend più forte
  - `days_above_ema`: aumentato a 5 per equity — richiede persistenza maggiore
- Regime BULL + Prezzo > SMA50: Obbigatori (aggiunto 02/07/2026)
- **EMA20 Slope ≥ 0.2%**: Filtro STRATO 2 per eliminare trend piatti (risultato: 92 L1 → 10 L1)
- Prezzo > SMA50: Obbligatorio (aggiunto 02/07/2026 nel codice)

### Uscita L1 — 6 Regole (in ordine di priorità)

| Priorità | Regola | Trigger | Tipo uscita | Asset class |
|:---:|--------|---------|:-----------:|-------------|
| 1 | **F — Kill Switch** | Calo giornaliero ≤ −3% | Totale | Tutte |
| 2 | **A — Stop Loss** | Prezzo sotto EMA20 da ≥ **3 giorni** consecutivi | Totale | Tutte |
| 3 | **B — Trailing Stop** | **EMA10 < EMA20** | Totale | Tutte |
| 4 | **C — Stanchezza** | RSI_prev ≥ 70 AND RSI_oggi < 70 | Totale | Non-bond |
| 5 | **E — ADX debole** | ADX < **18** AND prezzo < EMA20 | Totale | Equity/Commodity |
| 6 | **D — Uscita Parziale** | RSI > 78 | **Parziale 90%** | Equity/Commodity |

**Note importanti sulle regole:**
- **Regola A**: 3 giorni di tolleranza evitano uscite su falsi segnali da singolo giorno di panico
- **Regola B**: EMA10 < EMA20 è il trailing reattivo — molto più rapido del vecchio EMA20 < SMA50 (death cross tardivo)
- **Regola C**: solo per non-bond (i bond raramente toccano RSI 70); per bond si usa RSI < rsi_exit_min
- **Regola E**: condizione congiunta price < EMA20 evita uscite su consolidamenti laterali con ADX naturalmente basso
- **Regola D**: NON è uscita totale — attiva la logica "piede dentro"

### Logica "Piede Dentro" — 90% / 10%

```
Segnale D (RSI > 78):
  → USCITA PARZIALE: vendi 90% della posizione
  → Acquista ETF monetario (XEON — EUR Overnight €STR) con il 90%
  → Mantieni 10% ETF equity: rimane in L1, tracciato dalla dashboard

Segnale F / A / B / C / E:
  → USCITA TOTALE: vendi il 10% rimanente
  → Mantieni XEON fino al prossimo segnale di rientro

Rientro L1 (tutte 6 condizioni vere):
  → Vendi XEON → rientra 100% su ETF equity
  → Il 10% già presente non richiede riacquisto
```

**Vantaggi operativi:**
- Il 90% in XEON guadagna ~3–4% annuo (€STR) mentre si aspetta il rientro
- Il 10% rimasto è il "sensore": se il trend riprende si vede subito senza costi di riacquisto
- Tassazione solo sulla parte venduta (90%)
- Dashboard continua a tracciare l'ETF con dati reali anche durante la fase monetaria

### L0 — Deep Recovery (ETF in forte calo)

**Entrata** — 4 condizioni tutte obbligatorie:
1. Prezzo almeno `l0_drawdown`% sotto il picco storico (vedi profili: 8–20% per asset class)
2. RSI < `l0_rsi_max` (ipervenduto, vedi profili)
3. Divergenza rialzista (prezzo fa minimo più basso, RSI fa minimo più alto)
4. Segnale recupero: RSI risalito > 32 OPPURE micro-breakout ≥ 0.3% su 5gg

**Uscita L0** — basta 1:
- γ: Prezzo > EMA20 → promozione a L2
- β: RSI < 25 dopo ingresso → trappola ribassista
- α: Prezzo < panic_low (min 30gg al momento ingresso) → stop assoluto
- ε: Nessun recupero dopo 30gg → gestito in monitor.py

### Kill Switch ETF
Se variazione giornaliera ≤ −3%: nuovi ingressi L0 e L1 bloccati; uscite sempre operative.

### Indicatori calcolati (ETF)
- **EMA10** (period=10) — usato per exit rule B (trailing stop)
- **EMA20** (period=20) — media veloce, segnale principale
- **SMA50** (period=50) — media media, filtro allineamento
- **SMA200** (period=200) — filtro regime bear market
- **RSI14** — momentum oscillatore
- **ADX14** (da dati OHLCV reali) — forza direzionale
- **MACD** (12,26,9) — momentum condizione 6 entrata

---

## Schema Livelli Fondi (L0 / L1 / L2 / L3)

### L3 — Universe (monitoraggio passivo)
Tutti i fondi partono da qui. Nessuna condizione richiesta.

### L2 — Watchlist
- NAV sopra MM20 per ≥ 3 giorni consecutivi

### L1 — Core Portfolio ("Trend Sicuro") — 6 condizioni TUTTE obbligatorie
1. **Allineamento**: NAV > MM20 **E** MM20 > MM50
2. **Persistenza**: ≥ N giorni consecutivi sopra MM20 + slope MM20 positivo
3. **Momentum RSI**: RSI nel range ottimale (dipende da asset type, vedi sotto)
4. **Distanza MM20**: NAV sopra MM20 di max % (non troppo esteso)
5. **ADX**: > soglia (solo classi azionarie — per bond/monetari sempre OK)
6. **Pendenza NAV** (`nav_pendenza_ok`): ROC_3 > 0 **E** ROC_5 > 0 **E** rising_days ≥ 3
   - ROC_3: NAV oggi > NAV 3 giorni fa (variazione % strettamente positiva)
   - ROC_5: NAV oggi > NAV 5 giorni fa (variazione % strettamente positiva)
   - rising_days: almeno 3 chiusure in rialzo negli ultimi 5 giorni
   - *Blocca ingressi quando il NAV grezzo è già in pullback mentre la MM20 è ancora inerzialmente positiva*

> **Blocco ingresso L1**: se MM50 non calcolabile (storico < 50 giorni) → max L2

### Uscita L1 — 6 Regole (in ordine di priorità: F → D → A → B → E → C)
| Priorità | Regola | Trigger | Azionari/Comm. | Bond/HY | Money Mkt |
|:---:|--------|---------|:---:|:---:|:---:|
| 1 | F — Storico | MM50 non calcolabile | ✓ | ✓ | ✓ |
| 2 | D — Strutturale | MM20 < MM50 | ✓ | ✓ | ✓ |
| 3 | A — Stop Loss | NAV < MM20 | ✓ | **✗** | **✗** |
| 4 | B — Trailing Stop | MM5 < MM20 | ✓ | ✓ | ✓ |
| 5 | E — ADX debole | ADX < **20** + NAV < MM5 | ✓ | ✗ | ✗ |
| 6 | C — Stanchezza | RSI era ≥70, ora scende sotto 70 | ✓ | ✓ | **✗** |

**Note principali rispetto alla versione precedente:**
- Regola A disabilitata per bond/monetari (singolo giorno sotto MM20 è noise)
- Regola B diventa trailing principale per bond/monetari
- Regola E: soglia ADX abbassata da 25 a 20 + condizione congiunta NAV < MM5
- Regola C: soglia cambiata da ">75 e scende" a "era ≥70, ora <70" (uscita materiale dall'ipercomprato); disabilitata per money_market

### L0 — Deep Recovery (fondi in forte calo)
**Entrata** — 4 condizioni tutte obbligatorie:
1. NAV almeno **15% sotto il picco** storico disponibile
2. RSI < soglia oversold (dipende da asset type)
3. **Divergenza rialzista** (prezzo fa minimo più basso, RSI fa minimo più alto)
4. Segnale recupero: RSI risalito > 32 OPPURE micro-breakout ≥ 0.3% su 5gg

**Uscita** — basta 1:
- γ: NAV > MM20 → promozione naturale verso L2
- β: RSI < 25 dopo ingresso → trappola ribassista
- α: NAV < panic_low (min 30gg al momento ingresso) → stop loss assoluto
- ε: Nessun recupero dopo 30gg → gestito in monitor.py

### Kill Switch
Se variazione giornaliera NAV ≤ −3%: nuovi ingressi L0 e L1 bloccati; uscite sempre operative.

---

## Profili Asset Type — Parametri Tecnici

| Parametro | equity_developed | emerging_markets | sector_thematic | commodities | money_market | bond_government | bond_corporate | high_yield |
|-----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| MA period (MM20) | 20 | 20 | 20 | 20 | 20 | 20 | 20 | 20 |
| MA slow (MM50) | 50 | 50 | 50 | 50 | 50 | 50 | 50 | 50 |
| ADX threshold | 25 | 25 | 25 | 25 | n/a | n/a | n/a | n/a |
| RSI oversold | 30 | 30 | 30 | 30 | 25 | 35 | 35 | 33 |
| RSI overbought | 70 | 70 | 70 | 70 | 97 | 70 | 70 | 70 |
| **RSI ottimale L1** | **55–65** | **55–65** | **54–70** | **54–66** | **40–95** | **45–68** | **48–65** | **50–66** |
| Max dist MM20 | 2.5% | 3.0% | 3.5%* | 3.0% | 0.5% | 1.5% | 2.0% | 3.0% |
| Giorni sopra MM20 | 5 | 5 | 5 | 5 | 3 | 3 | 3 | 3 |
| Bollinger σ | 2.0 | 2.0 | 2.0 | 2.0 | 1.0 | 1.5 | 1.7 | 1.8 |
| RSI stanchezza (Regola C) | >75 | >75 | >75 | >75 | **>92** | >75 | >75 | >75 |

> **money_market**: RSI strutturalmente alto (80-90) per natura dello strumento (NAV cresce ~linearmente). Non indica ipercomprato. ADX non richiesto. Regola C alzata a >92 per evitare uscite premature su normali oscillazioni RSI.
> **sector_thematic** *: Max dist MM20 3.5% ordinaria; sale a **5%** se breakout giornaliero ≥+3.5% con RSI<75 e MM20>MM50 (override per strappi violenti tipici dei fondi tematici).

### Rilevamento asset_type da categoria Excel
> **Ordine di priorità** nel codice: money_market → sector_thematic → high_yield → emerging → commodities → bond → equity_developed
> Il check `sector_thematic` è **anticipato** rispetto a `high_yield` per evitare falsi match (es. "Energie **Alternative**" contiene `alternativ` ma è settoriale).

| Categoria contiene | → asset_type |
|--------------------|--------------|
| `monetar`, `liquidity`, `money market` | `money_market` |
| `settorial`, `thematic`, `tecnolog`, `healthcare`, `salute`, `energia`, `infrastruttur`, `real estate`, `immobil`, `biotech`, `pharma`, `fintech`, `consumi`, `lusso`, `consumer`, `acqua`, `water`, `agri`, `food`, `nutrizi`, `cyber`, `security`, `biotecnolog`, `farmac` | `sector_thematic` |
| `alternativ`, `bilanc`, `multi-asset`, `absolute return` | `high_yield` |
| `emerging`, `cina`, `india`, `asia pacific` | `emerging_markets` |
| `materie prime`, `oro`, `gold`, `commodity`, `metalli`, `petrolio` | `commodities` |
| `high yield`, `high-yield` | `high_yield` |
| `corporate`, `credit` | `bond_corporate` |
| `obblig`, `bond`, `fixed` (altri) | `bond_government` |
| tutto il resto | `equity_developed` |

> **Bug storico corretto (21/05/2026)**: il codice cercava `settoriale` (singolare) che non matchava `settoriali` (plurale) nelle categorie Excel — 18 fondi erano mal classificati come `equity_developed`.

### Benchmark per signal purity
| asset_type | Benchmark |
|------------|-----------|
| equity_developed | IWDA.L — MSCI World |
| emerging_markets | EIMI.L — MSCI EM |
| sector_thematic | IWDA.L (proxy) |
| commodities | CMOD.L |
| money_market | XEON.L — EUR Overnight Rate (ESTER) |
| bond_government | XGLE.L — Eurozone Govt Bond |
| bond_corporate | IEAA.L — EUR Corporate Bond |
| high_yield | IHYG.L — EUR High Yield |

---

## Flusso Monitor Quotidiano (18:00 CEST run principale, 09:00 CEST run silenzioso)

```
scheduler.py
  └─ monitor.py::run_monitor()
       ├─ Per ogni ISIN in fondi_monitoraggio.xlsx (foglio "Fondi"):
       │    ├─ data_fetcher.get_nav() → FT Markets → salva in PostgreSQL
       │    ├─ db.get_price_series(isin, days=100) → prezzi storici
       │    ├─ technical_analysis.suggest_level() → L0/L1/L2/L3
       │    ├─ technical_analysis.suggest_level_0() → analisi L0
       │    └─ Se livello cambia → aggiorna Excel (colonna Livello)
       ├─ Salva data/dashboard_data.json (letto da app.py)
       ├─ alerts.py → email Resend (digest L1, uscite L1, digest L0)
       └─ Aggiorna l0_tracking e l1_tracking nel DB
```

### Struttura `dashboard_data.json`
```json
{
  "levels": {
    "1": [ { "isin": "...", "nome": "...", "rsi": 58.1, "ma": 105.2, ... } ],
    "2": [ ... ],
    "3": [ ... ]
  },
  "l0_funds": [ ... ],
  "summary": { "buy_signals": 5, "sell_signals": 2, "hold_signals": 120 }
}
```
> Il livello è la **chiave** del dizionario, non un campo interno agli oggetti.

---

## Excel `fondi_monitoraggio.xlsx`

### Foglio "Fondi" — colonne
| # | Nome | Tipo | Note |
|---|------|------|------|
| 1 | Livello | int | 0/1/2/3 — aggiornato automaticamente dal monitor |
| 2 | ISIN | str | Codice ISIN europeo |
| 3 | Nome Fondo | str | |
| 4 | Casa Gestione | str | |
| 5 | Categoria | str | Usata per rilevare asset_type |
| 6 | Valuta | str | |
| 7 | Prezzo | float | Ultimo NAV |
| 8 | MM15 | float | Storico (vecchio campo, ora calcolato dinamicamente) |
| 9 | RSI | float | Storico (vecchio campo) |
| 10 | Segnale | str | BUY/HOLD/SELL (storico) |
| 11 | Ultima Modifica | str | Timestamp |

---

## Note operative importanti

- `docker compose` (senza trattino) su Ubuntu 24.04
- Per installare pacchetti Python globali: `pip3 install X --break-system-packages`
- Il container **attivo** Fondi è `fund-monitor-app-1` (porta 5000) — NON usare `fund_monitor_system-app-1` (porta 5002, dati vecchi Feb 2026)
- Il container **attivo** ETF è `etf_monitor_system-app-1` (porta 5001)
- **CRITICO — dopo `docker cp` su file `.py`: sempre `docker restart <container>`** — Python non ricarica moduli live, il vecchio codice rimane in memoria fino al riavvio
- Cloudflare SSL deve essere **Full (strict)** — mai "Flexible"
- DNS record A: `fondi` e `etf` → `76.13.37.133` (Proxied su Cloudflare)
- Email: account Resend su `andreapavan67@gmail.com`, piano gratuito, sender `onboarding@resend.dev`
- La memoria automatica di Claude è in `memory/MEMORY.md` (infrastruttura + preferenze)
- **Git pull VPS su ETF**: il monitor modifica `etf_monitoraggio.xlsx` e `dashboard.html` in-place → `git checkout -- <file>` prima di `git pull`
- **Git remote ETF**: `origin` = `git@github-pimpy67:pimpy67/etf-monitor-system.git`
- **Git remote Fondi**: `origin` = `git@github-pimpy67:pimpy67/monitoraggio-fondi.git`

# CLAUDE.md — Monitoraggio Fondi & ETF

Documento di riferimento tecnico per il progetto. Caricato automaticamente a ogni sessione.

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
| Sorgente codice (Mac) | `/Users/user/Documents/CORSO_ITS/APPLICAZIONI _ APP/MONITORAGGIO FONDI/fund_monitor_system/` |
| Sorgente codice (VPS) | `/root/fund_monitor_system/` |
| Container **attivo** | `fund-monitor-app-1` → porta **5000** |
| Container secondario | `fund_monitor_system-app-1` → porta 5002 (dati vecchi, NON usato) |
| Nginx config | `/etc/nginx/sites-enabled/fondi` → proxy a **porta 5000** (non cambiare) |
| Dashboard | `https://fondi.andreapavan.tech` |

### Database PostgreSQL
- Container: `fund-monitor-postgres-1`
- Credenziali: user `fundmonitor`, db `funds`, password in `.env` → `DB_PASSWORD`
- **Tabelle**:
  - `price_history` — storico NAV (isin, date, price, source) — UNIQUE su (isin, date)
  - `l0_tracking` — fondi in recupero L0 (entry_price, entry_date, panic_low)
  - `l1_tracking` — fondi in trend sicuro L1
  - `l1_exit_history` — storico uscite L1 (exit_date, exit_rule, entry_date, pct_gain)
  - `portfolio_entries` — portafoglio personale (isin, entry_date, entry_price, fund_name)

### Comandi rapidi
```bash
# Copia file Mac → VPS
scp "/Users/user/Documents/CORSO_ITS/APPLICAZIONI _ APP/MONITORAGGIO FONDI/fund_monitor_system/<file>" root@76.13.37.133:/root/fund_monitor_system/

# Copia file VPS → container attivo
docker cp /root/fund_monitor_system/<file> fund-monitor-app-1:/app/

# Riavvia container
docker restart fund-monitor-app-1

# Log live
docker logs fund-monitor-app-1 --tail=30 -f

# Trigger manuale monitor
curl -X POST http://localhost:5000/api/trigger-update

# Query DB direttamente
docker exec fund-monitor-postgres-1 psql -U fundmonitor -d funds -c "<SQL>"
```

### File principali
| File | Ruolo |
|------|-------|
| `app.py` | Flask API + serving dashboard |
| `monitor.py` | Logica principale: fetch NAV, calcolo livelli, aggiorna Excel, salva DB |
| `technical_analysis.py` | Indicatori tecnici, profili asset, logica L0/L1/L2/L3 |
| `data_fetcher.py` | Fetch NAV da FT Markets (primario) e Yahoo Finance (backup) |
| `database.py` | Wrapper PostgreSQL |
| `scheduler.py` | Job scheduler — 18:00 lun-ven |
| `dashboard.html` | Frontend SPA (HTML+JS, servito da Flask) |
| `fondi_monitoraggio.xlsx` | Excel con tutti i fondi — fonte di verità per lista e livelli |
| `backfill_historical.py` | Backfill storico da FT Markets (richiede 210gg, ottiene ~20-22gg reali) |

### Variabili d'ambiente `.env`
```
RESEND_API_KEY=...
EMAIL_SENDER=onboarding@resend.dev
EMAIL_RECIPIENT=andreapavan67@gmail.com
DB_PASSWORD=FundMonitor2026!
MONITOR_HOUR=18
MONITOR_MINUTE=0
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
| Sorgente codice (VPS) | `/root/etf_monitor_system/` |
| Docker Compose | porta **5001** |
| Dashboard | `https://etf.andreapavan.tech` |
| Nginx config | `/etc/nginx/sites-enabled/etf` |

### Caratteristiche ETF vs Fondi
- ETF usa **Yahoo Finance OHLCV** (ticker formato Yahoo, es. `SWDA.L`, `ENRJ.MI`)
- Indicatori: EMA20, SMA50, SMA200, ADX14, RSI14
- SMA200 come **filtro regime bear market** (se ETF sotto SMA200 → no nuovi ingressi L1)
- 195 ETF analizzati
- Database: PostgreSQL in Docker (user: etfmonitor, db: etfs), tabelle: `etf_price_history`, `etf_l1_tracking`, `etf_l0_tracking`

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
| **RSI ottimale L1** | **55–65** | **55–65** | **54–66** | **54–66** | **40–95** | **45–68** | **48–65** | **50–66** |
| Max dist MM20 | 2.5% | 3.0% | 2.5% | 3.0% | 0.5% | 1.5% | 2.0% | 3.0% |
| Giorni sopra MM20 | 5 | 5 | 5 | 5 | 3 | 3 | 3 | 3 |
| Bollinger σ | 2.0 | 2.0 | 2.0 | 2.0 | 1.0 | 1.5 | 1.7 | 1.8 |
| RSI stanchezza (Regola C) | >75 | >75 | >75 | >75 | **>92** | >75 | >75 | >75 |

> **money_market**: RSI strutturalmente alto (80-90) per natura dello strumento (NAV cresce ~linearmente). Non indica ipercomprato. ADX non richiesto. Regola C alzata a >92 per evitare uscite premature su normali oscillazioni RSI.

### Rilevamento asset_type da categoria Excel
| Categoria contiene | → asset_type |
|--------------------|--------------|
| `monetar`, `liquidity`, `money market` | `money_market` |
| `alternativ`, `bilanc`, `multi-asset`, `absolute return` | `high_yield` |
| `emerging`, `cina`, `india`, `asia pacific` | `emerging_markets` |
| `tecnolog`, `healthcare`, `real estate`, `settoriale` | `sector_thematic` |
| `materie prime`, `oro`, `gold`, `commodity` | `commodities` |
| `high yield`, `high-yield` | `high_yield` |
| `corporate`, `credit` | `bond_corporate` |
| `obblig`, `bond`, `fixed` (altri) | `bond_government` |
| tutto il resto | `equity_developed` |

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

## Flusso Monitor Quotidiano (18:00 lun-ven)

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
- Il container **attivo** è `fund-monitor-app-1` (porta 5000) — NON usare `fund_monitor_system-app-1` (porta 5002, dati vecchi Feb 2026)
- Dopo modifiche a Python: `docker cp file fund-monitor-app-1:/app/` + `docker restart fund-monitor-app-1`
- Cloudflare SSL deve essere **Full (strict)** — mai "Flexible"
- DNS record A: `fondi` e `etf` → `76.13.37.133` (Proxied su Cloudflare)
- Email: account Resend su `andreapavan67@gmail.com`, piano gratuito, sender `onboarding@resend.dev`
- La memoria automatica di Claude è in `memory/MEMORY.md` (infrastruttura + preferenze)

# Fund Monitor System

Sistema automatizzato di monitoraggio fondi a 4 livelli con alert email e dashboard web.

Dashboard: **https://fondi.andreapavan.tech**

---

## Infrastruttura

- **VPS**: Hostinger Ubuntu 24.04 LTS — `76.13.37.133`
- **Container Docker**: `fund-monitor-app-1` → porta 5000
- **Database**: PostgreSQL in Docker
- **Reverse proxy**: Nginx + Cloudflare (SSL Full strict)
- **Email**: Resend API (`onboarding@resend.dev`)

---

## Deploy

```bash
./deploy.sh
```

Fa in sequenza: `git push` → SSH git reset sul VPS → docker build → docker up.

---

## Struttura file

```
├── app.py                  # Flask API + serving dashboard
├── monitor.py              # Logica principale: fetch NAV, calcolo livelli
├── technical_analysis.py   # Indicatori tecnici, logica L0/L1/L2/L3
├── data_fetcher.py         # Fetch NAV da FT Markets (backup: Yahoo Finance)
├── database.py             # Wrapper PostgreSQL
├── scheduler.py            # Scheduler: 18:00 CEST principale, 09:00 silenzioso
├── alerts.py               # Email Resend: nuovi L1/L0, uscite, segnali portafoglio
├── dashboard.html          # Frontend SPA
├── fondi_monitoraggio.xlsx # Excel master — fonte di verità per lista fondi
├── deploy.sh               # Script deploy completo
├── docker-compose.yml
└── etf_monitor_system/     # Sistema ETF separato (repo git proprio)
```

---

## Livelli

| Livello | Nome | Descrizione |
|---------|------|-------------|
| **L3** | Universe | Tutti i fondi — monitoraggio passivo |
| **L2** | Watchlist | NAV sopra MM20 da ≥3 giorni |
| **L1** | Core Portfolio | 6 condizioni tecniche tutte soddisfatte — trend confermato |
| **L0** | Deep Recovery | Fondo in forte calo con segnali di rimbalzo |

### Condizioni entrata L1 (tutte obbligatorie)
1. Allineamento: NAV > MM20 > MM50
2. Persistenza: ≥5 giorni sopra MM20 + slope MM20 positivo
3. RSI nel range ottimale per asset class
4. Distanza MM20 ≤ soglia (non troppo esteso)
5. ADX > soglia (solo azionari)
6. Pendenza NAV: ROC_3 > 0 e ROC_5 > 0 e ≥3 giorni in rialzo su 5

---

## Gestione Excel (`fondi_monitoraggio.xlsx`)

Il monitor legge la lista fondi dall'Excel e aggiorna automaticamente la colonna **Livello**.

**Non modificare** la colonna Livello manualmente — viene sovrascritta dal monitor.

Per aggiungere un fondo: aggiungi riga nel foglio "Fondi" con ISIN, Nome, Casa Gestione, Categoria, Valuta. Il monitor lo raccoglierà al run successivo.

Per la lista delle categorie riconosciute e il mapping verso asset_type, vedere `CLAUDE.md`.

---

## Email alert

- **Nuovi ingressi L1/L0** — email immediata
- **Uscite L1** — email con regola di uscita e gain/loss
- **Segnali portafoglio** — RSI tirato, stanchezza, ATH
- Scheduler: run principale **18:00 CEST** con alert; run silenzioso **09:00 CEST** senza alert

---

## Comandi rapidi

```bash
# Log live
ssh root@76.13.37.133 "docker logs fund-monitor-app-1 --tail=50 -f"

# Trigger monitor manuale
ssh root@76.13.37.133 "curl -s -X POST http://localhost:5000/api/trigger-update"

# Query DB
ssh root@76.13.37.133 "docker exec fund-monitor-postgres-1 psql -U fundmonitor -d funds -c '<SQL>'"
```

---

> I segnali sono informativi, non consulenza finanziaria.

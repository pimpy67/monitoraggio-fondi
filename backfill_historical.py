"""
backfill_historical.py - Backfill storico ultimi 50 giorni
-------------------------------------------------------
Per ogni ISIN nel foglio `Fondi` prova a recuperare lo storico
con `FundDataFetcher.get_historical_nav(isin, days=60)` e salva
gli ultimi 50 giorni su DB (UPSERT) o in `data/history/<ISIN>.json`.
"""

import os
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from data_fetcher import FundDataFetcher
from database import PriceDatabase


def ensure_dirs():
    os.makedirs('data', exist_ok=True)
    os.makedirs('data/history', exist_ok=True)


def save_local_history(isin: str, records):
    history_file = Path('data/history') / f"{isin}.json"
    history = []
    if history_file.exists():
        try:
            with open(history_file, 'r') as fh:
                history = json.load(fh)
        except Exception:
            history = []

    # Merge preserving order by date, avoid duplicates
    existing_dates = {h['date'] for h in history}
    for r in records:
        if r['date'] not in existing_dates:
            history.append(r)

    # Keep most recent 365
    history = sorted(history, key=lambda x: x['date'])[-365:]
    with open(history_file, 'w') as fh:
        json.dump(history, fh)


def main():
    ensure_dirs()

    excel_path = 'fondi_monitoraggio.xlsx'
    if not os.path.exists(excel_path):
        print(f"Errore: file Excel non trovato: {excel_path}")
        return

    df = pd.read_excel(excel_path, sheet_name='Fondi')
    if df.empty:
        print("Nessun fondo trovato nel file Excel")
        return

    fetcher = FundDataFetcher()
    db = PriceDatabase()

    stats = {'db_saved': 0, 'local_saved': 0, 'not_found': 0}

    for idx, row in df.iterrows():
        isin = str(row.get('ISIN')).strip()
        nome = row.get('Nome Fondo', '')
        if not isin or isin.lower() in ['nan', 'none']:
            continue

        print(f"\n[{idx+1}/{len(df)}] Processing {isin} - {str(nome)[:40]}...")

        try:
            hist_df = fetcher.get_historical_nav(isin, days=210)
        except Exception as e:
            print(f"  Errore fetch storico per {isin}: {e}")
            hist_df = pd.DataFrame()

        if hist_df is None or hist_df.empty:
            print(f"  ❌ Storico non disponibile per {isin}")
            stats['not_found'] += 1
            continue

        # Ensure proper columns
        if 'date' not in hist_df.columns or ('nav' not in hist_df.columns and 'nav' not in hist_df.columns):
            print(f"  ❌ Formato storico inatteso per {isin}")
            stats['not_found'] += 1
            continue

        # Use 'nav' column
        try:
            hist_df['date'] = pd.to_datetime(hist_df['date']).dt.strftime('%Y-%m-%d')
            hist_df = hist_df.sort_values('date')
        except Exception:
            pass

        # Take last 200 days
        recent = hist_df.tail(200)

        records = []
        for _, r in recent.iterrows():
            try:
                price = float(r['nav'])
                date = str(r['date'])
                records.append({'date': date, 'price': price, 'source': 'Yahoo/Historical'})
            except Exception:
                continue

        if not records:
            print(f"  ❌ Nessun record utile per {isin}")
            stats['not_found'] += 1
            continue

        # Try save to DB (UPSERT per record)
        saved_db = 0
        saved_local = 0
        for rec in records:
            try:
                ok = db.save_price(isin, rec['date'], rec['price'], rec.get('source', 'Historical'))
            except Exception:
                ok = False

            if ok:
                saved_db += 1
            else:
                saved_local += 1

        if saved_db > 0:
            stats['db_saved'] += saved_db
            print(f"  💾 Salvati {saved_db} record in DB per {isin}")
        if saved_local > 0:
            # Save those records locally
            try:
                save_local_history(isin, [ {'date': r['date'], 'price': r['price'], 'source': r.get('source','Historical')} for r in records ])
                stats['local_saved'] += saved_local
                print(f"  💾 Salvati {saved_local} record localmente per {isin}")
            except Exception as e:
                print(f"  ❌ Errore salvataggio locale per {isin}: {e}")

    print("\nBackfill storico completato:")
    print(json.dumps(stats, indent=2))


if __name__ == '__main__':
    main()

"""
app.py - Web server per la dashboard
=====================================
Serve la dashboard HTML e i dati JSON.
Legge dati direttamente da PostgreSQL (persistente).
Include auto-recovery: se il monitoraggio non ha girato oggi, lo lancia automaticamente.
"""

from flask import Flask, send_file, send_from_directory, jsonify, request, make_response
import os
import json
import threading
from datetime import datetime, date

from database import PriceDatabase
import monitor_lock

app = Flask(__name__)

# Database instance globale
db = PriceDatabase()


@app.route('/')
def index():
    """Serve la dashboard principale (no cache — aggiornamenti sempre freschi)"""
    resp = make_response(send_file('dashboard.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/data/<path:filename>')
def serve_data(filename):
    """Serve i file dati JSON"""
    return send_from_directory('data', filename)

def _get_last_update():
    """Legge la data dell'ultimo aggiornamento dal file JSON"""
    try:
        with open('data/dashboard_data.json', 'r') as f:
            data = json.load(f)
        return data.get('last_update')
    except:
        return None


def _should_run_today():
    """Controlla se il monitoraggio deve girare oggi.
    Ritorna True se:
    - Non ha mai girato, oppure
    - L'ultimo aggiornamento non e' di oggi E siamo dopo l'orario programmato
    - Il file esiste ma ha 0 fondi (file iniziale vuoto creato al deploy)
    """
    now = datetime.now()
    monitor_hour = int(os.environ.get('MONITOR_HOUR', 18))

    try:
        with open('data/dashboard_data.json', 'r') as f:
            data = json.load(f)
        last_update = data.get('last_update')
        total_funds = data.get('summary', {}).get('total_funds', 0)

        # Se ha 0 fondi, il file e' vuoto (creato al deploy) → deve girare
        if total_funds == 0:
            return True

        if not last_update:
            return True

        last_dt = datetime.fromisoformat(last_update)
        # Se l'ultimo aggiornamento non e' di oggi E siamo dopo l'ora programmata
        if last_dt.date() < now.date() and now.hour >= monitor_hour:
            return True
    except:
        return True

    return False


def _trigger_auto_monitor():
    """Lancia il monitoraggio in background se non sta gia' girando"""
    if not monitor_lock.try_acquire():
        return False

    def run():
        try:
            print(f"\n🔄 AUTO-RECOVERY: Monitoraggio automatico avviato - {datetime.now()}")
            from monitor import FundMonitor
            monitor = FundMonitor()
            monitor.run(send_daily_report=True)
            print(f"✅ AUTO-RECOVERY: Monitoraggio completato - {datetime.now()}")
        except Exception as e:
            print(f"❌ AUTO-RECOVERY: Errore monitoraggio: {e}")
        finally:
            monitor_lock.release()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return True


@app.route('/api/status')
def status():
    """Endpoint per verificare lo stato del sistema (+ auto-recovery)"""
    # Prova prima il file JSON (generato dal monitor)
    json_status = None
    try:
        with open('data/dashboard_data.json', 'r') as f:
            data = json.load(f)
        json_status = {
            'last_update': data.get('last_update'),
            'total_funds': data.get('summary', {}).get('total_funds', 0)
        }
    except:
        pass

    # Controlla anche lo stato del database
    db_stats = db.get_stats()

    # AUTO-RECOVERY: se il monitoraggio non ha girato oggi, lancialo
    auto_recovery_triggered = False
    if _should_run_today() and not monitor_lock.is_running():
        auto_recovery_triggered = _trigger_auto_monitor()

    return jsonify({
        'status': 'ok',
        'json_data': json_status,
        'database': db_stats,
        'database_url_set': bool(os.environ.get('DATABASE_URL')),
        'monitor_running': monitor_lock.is_running(),
        'auto_recovery_triggered': auto_recovery_triggered
    })

@app.route('/api/funds')
def get_funds():
    """API per ottenere tutti i fondi - legge da JSON generato dal monitor"""
    try:
        with open('data/dashboard_data.json', 'r') as f:
            return jsonify(json.load(f))
    except:
        return jsonify({'error': 'Data not available'}), 404

@app.route('/api/db-status')
def db_status_endpoint():
    """Diagnostica connessione database PostgreSQL"""
    raw_url = os.environ.get('DATABASE_URL', '')

    # Mostra URL mascherato (senza password)
    safe_url = 'NOT SET'
    if raw_url:
        import re
        safe_url = re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', raw_url)

    result = {
        'database_url_safe': safe_url,
        'database_url_length': len(raw_url),
        'database_url_starts_with': raw_url[:20] if raw_url else 'NOT SET',
        'db_url_resolved': bool(db.database_url),
        'timestamp': datetime.now().isoformat()
    }

    # Test connessione FRESCA (non usa cache)
    try:
        import psycopg2
        # Prova connessione diretta
        try:
            conn = psycopg2.connect(raw_url, sslmode='require', connect_timeout=5)
            conn.close()
            result['connection'] = 'OK (SSL)'
        except Exception as ssl_err:
            result['ssl_error'] = str(ssl_err)
            try:
                conn = psycopg2.connect(raw_url, connect_timeout=5)
                conn.close()
                result['connection'] = 'OK (no SSL)'
            except Exception as no_ssl_err:
                result['connection'] = 'ERRORE'
                result['no_ssl_error'] = str(no_ssl_err)
    except Exception as e:
        result['connection'] = 'ERRORE'
        result['error'] = str(e)

    return jsonify(result)

@app.route('/api/prices')
def get_prices():
    """API per ottenere prezzi dal database PostgreSQL"""
    isin = request.args.get('isin')
    days = int(request.args.get('days', 30))

    if isin:
        # Prezzi per un singolo fondo
        df = db.get_prices(isin, days)
        if not df.empty:
            prices = []
            for _, row in df.iterrows():
                prices.append({
                    'date': str(row['date']),
                    'price': float(row['price'])
                })
            return jsonify({'isin': isin, 'prices': prices, 'count': len(prices)})
        return jsonify({'isin': isin, 'prices': [], 'count': 0})
    else:
        # Tutti i prezzi (statistiche)
        stats = db.get_stats()
        return jsonify(stats)

@app.route('/api/trigger-update', methods=['GET', 'POST'])
def trigger_update():
    """Trigger manuale del monitoraggio (GET o POST)"""
    started = _trigger_auto_monitor()

    if started:
        return jsonify({
            'status': 'started',
            'message': 'Monitoraggio avviato in background',
            'timestamp': datetime.now().isoformat()
        })
    else:
        return jsonify({
            'status': 'already_running',
            'message': 'Monitoraggio gia\' in esecuzione, attendere il completamento',
            'timestamp': datetime.now().isoformat()
        })


@app.route('/api/monitor-log')
def get_monitor_log():
    """Mostra il log del monitoraggio"""
    from monitor import monitor_log
    return jsonify({
        'log': monitor_log,
        'count': len(monitor_log),
        'monitor_running': monitor_lock.is_running(),
        'timestamp': datetime.now().isoformat()
    })


@app.route('/api/health')
def health_check():
    """Endpoint completo di health check del sistema"""
    now = datetime.now()

    # 1. Leggi health dal dashboard_data.json (generato dal monitor)
    health = {}
    last_update = None
    try:
        with open('data/dashboard_data.json', 'r') as f:
            data = json.load(f)
        health = data.get('health', {})
        last_update = data.get('last_update')
    except Exception:
        pass

    # 2. Calcola freshness (quanto e' vecchio l'ultimo aggiornamento)
    stale = True
    hours_since_update = None
    if last_update:
        try:
            last_dt = datetime.fromisoformat(last_update)
            delta = now - last_dt
            hours_since_update = round(delta.total_seconds() / 3600, 1)
            # Consideriamo fresco se aggiornato nelle ultime 24h
            stale = hours_since_update > 24
        except Exception:
            pass

    # 3. Database status
    db_ok = False
    db_stats = {}
    try:
        db_ok = db.is_available()
        if db_ok:
            db_stats = db.get_stats()
    except Exception:
        pass

    # 3.5 AUTO-RECOVERY: se dati stale e monitoraggio non in corso, lancialo
    auto_recovery_triggered = False
    if stale and _should_run_today() and not monitor_lock.is_running():
        print(f"🔄 HEALTH AUTO-RECOVERY: dati vecchi di {hours_since_update}h, lancio monitoraggio...")
        auto_recovery_triggered = _trigger_auto_monitor()

    # 4. Calcola stato globale
    funds_ok = health.get('funds_ok', 0)
    funds_error = health.get('funds_error', 0)
    total_funds = health.get('total_funds', 0)
    funds_with_price = health.get('funds_with_price', 0)

    if not stale and funds_error == 0 and db_ok and funds_with_price == funds_ok:
        status = 'green'
        message = 'Sistema operativo - tutti i fondi aggiornati'
    elif stale:
        status = 'red'
        message = f'Dati non aggiornati da {hours_since_update}h'
    elif funds_error > 0 or not db_ok:
        status = 'yellow'
        problems = []
        if funds_error > 0:
            problems.append(f'{funds_error} fondi con errore')
        if not db_ok:
            problems.append('DB non raggiungibile')
        message = ' | '.join(problems)
    else:
        status = 'yellow'
        message = 'Stato parziale'

    return jsonify({
        'status': status,
        'message': message,
        'last_update': last_update,
        'hours_since_update': hours_since_update,
        'stale': stale,
        'monitor_running': monitor_lock.is_running(),
        'database': {
            'connected': db_ok,
            'stats': db_stats
        },
        'funds': {
            'total': total_funds,
            'analyzed_ok': funds_ok,
            'errors': funds_error,
            'with_price': funds_with_price,
            'no_price': health.get('funds_no_price', 0),
            'error_details': health.get('errors', [])
        },
        'auto_recovery_triggered': auto_recovery_triggered,
        'checked_at': now.isoformat()
    })


@app.route('/api/backfill')
def backfill():
    """Backfill storico prezzi: recupera ultimi 50 giorni da Yahoo Finance e salva in DB"""
    if not db.is_available():
        return jsonify({'error': 'Database non disponibile'}), 503

    days = int(request.args.get('days', 50))

    def run_backfill():
        import pandas as pd
        from data_fetcher import FundDataFetcher

        fetcher = FundDataFetcher()
        stats = {'db_saved': 0, 'not_found': 0, 'errors': 0, 'funds_processed': 0}

        try:
            excel_df = pd.read_excel('fondi_monitoraggio.xlsx', sheet_name='Fondi')
        except Exception as e:
            print(f"Backfill: errore lettura Excel: {e}")
            return

        for _, row in excel_df.iterrows():
            isin = str(row.get('ISIN', '')).strip()
            nome = str(row.get('Nome Fondo', ''))[:40]
            if not isin or isin.lower() in ['nan', 'none']:
                continue

            stats['funds_processed'] += 1
            print(f"  Backfill {isin} - {nome}...")

            try:
                hist_df = fetcher.get_historical_nav(isin, days=days + 10)
            except Exception:
                hist_df = pd.DataFrame()

            if hist_df is None or hist_df.empty:
                stats['not_found'] += 1
                continue

            if 'date' not in hist_df.columns or 'nav' not in hist_df.columns:
                stats['not_found'] += 1
                continue

            try:
                hist_df['date'] = pd.to_datetime(hist_df['date']).dt.strftime('%Y-%m-%d')
                hist_df = hist_df.sort_values('date')
            except Exception:
                pass

            for _, r in hist_df.tail(days).iterrows():
                try:
                    ok = db.save_price(isin, str(r['date']), float(r['nav']), 'Yahoo/Backfill')
                    if ok:
                        stats['db_saved'] += 1
                except Exception:
                    stats['errors'] += 1

        print(f"Backfill completato: {stats}")

    thread = threading.Thread(target=run_backfill, daemon=True)
    thread.start()

    return jsonify({
        'status': 'started',
        'message': f'Backfill storico ultimi {days} giorni avviato in background',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/api/test-fund')
def test_fund():
    """Test di debug: prova ad analizzare un singolo fondo"""
    import traceback
    result = {'steps': []}

    try:
        # Step 1: carica Excel
        from monitor import FundMonitor
        monitor = FundMonitor()
        result['steps'].append('Excel FundMonitor creato OK')

        df = monitor.load_funds()
        result['steps'].append(f'Excel caricato: {len(df)} fondi')
        result['columns'] = list(df.columns)

        if df.empty:
            result['error'] = 'DataFrame vuoto'
            return jsonify(result)

        # Step 2: prendi il primo fondo
        row = df.iloc[0]
        result['test_fund'] = {
            'isin': str(row['ISIN']),
            'nome': str(row['Nome Fondo']),
            'livello': int(row['Livello'])
        }
        result['steps'].append(f"Fondo selezionato: {row['Nome Fondo']}")

        # Step 3: fetch NAV
        nav = monitor.data_fetcher.get_nav(row['ISIN'])
        result['nav_result'] = nav
        result['steps'].append(f"NAV: {nav}")

        # Step 4: get history from DB
        prices = monitor.db.get_price_series(row['ISIN'], days=100)
        result['price_count'] = len(prices)
        result['steps'].append(f"Storico DB: {len(prices)} prezzi")

        # Step 5: analyze
        analysis = monitor.analyze_fund(row)
        result['analysis'] = str(analysis)
        result['steps'].append('Analisi completata OK')

    except Exception as e:
        result['error'] = str(e)
        result['traceback'] = traceback.format_exc()

    return jsonify(result)


@app.route('/api/portfolio', methods=['GET'])
def get_portfolio():
    """Restituisce tutti i fondi nel portafoglio personale, arricchiti con dati attuali."""
    entries = db.get_portfolio_entries()

    # Carica dati di analisi correnti dal JSON per arricchire il portafoglio
    fund_analysis = {}
    try:
        with open('data/dashboard_data.json', 'r') as f:
            dash = json.load(f)
        for level_key, level_funds in dash.get('levels', {}).items():
            for fund in level_funds:
                isin = fund.get('isin') or fund.get('ISIN') or ''
                if isin:
                    fund_analysis[isin] = {**fund, 'livello': int(level_key)}
        for fund in dash.get('l0_funds', []):
            isin = fund.get('isin') or ''
            if isin:
                fund_analysis[isin] = {**fund, 'livello': 0}
    except Exception:
        pass

    result = []
    for entry in entries:
        isin = entry['isin']
        analysis = fund_analysis.get(isin, {})

        # Recupera l'ultimo NAV dalla price_history
        prices_df = db.get_prices(isin, days=5)
        current_nav = None
        if not prices_df.empty:
            current_nav = float(prices_df.iloc[-1]['price'])

        perf_pct = None
        if current_nav and entry['entry_price'] and float(entry['entry_price']) > 0:
            perf_pct = round((current_nav - float(entry['entry_price'])) / float(entry['entry_price']) * 100, 2)

        exit_nav = entry.get('exit_price')
        exit_date = entry.get('exit_date')
        status = entry.get('status', 'active')

        # P&L su prezzo di uscita se già uscito, altrimenti su NAV attuale
        ref_price = exit_nav if (status == 'exited' and exit_nav) else current_nav
        perf_pct = None
        if ref_price and entry['entry_price'] and float(entry['entry_price']) > 0:
            perf_pct = round((float(ref_price) - float(entry['entry_price'])) / float(entry['entry_price']) * 100, 2)

        result.append({
            'isin': isin,
            'fund_name': entry['fund_name'],
            'entry_date': entry['entry_date'],
            'entry_price': entry['entry_price'],
            'current_nav': current_nav,
            'perf_pct': perf_pct,
            'exit_date': exit_date,
            'exit_price': exit_nav,
            'status': status,
            'level': analysis.get('livello') or analysis.get('level'),
            'signal': analysis.get('segnale') or analysis.get('signal'),
            'rsi': analysis.get('rsi14') or analysis.get('rsi'),
            'mm20': analysis.get('mm20') or analysis.get('sma20'),
            'dist_mm20': analysis.get('distanza_mm20') or analysis.get('dist_sma20_pct'),
            'adx': analysis.get('adx14') or analysis.get('adx'),
            'asset_type': analysis.get('asset_type', ''),
        })

    return jsonify({'portfolio': result, 'count': len(result)})


@app.route('/api/portfolio', methods=['POST'])
def add_to_portfolio():
    """Aggiunge un fondo al portafoglio personale."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Dati mancanti'}), 400

    isin = data.get('isin', '').strip().upper()
    entry_date = data.get('entry_date', '')
    entry_price = data.get('entry_price')
    fund_name = data.get('fund_name', '').strip()

    if not isin or not entry_date or entry_price is None:
        return jsonify({'error': 'isin, entry_date e entry_price sono obbligatori'}), 400

    try:
        entry_price = float(entry_price)
    except (ValueError, TypeError):
        return jsonify({'error': 'entry_price deve essere un numero'}), 400

    ok = db.add_portfolio_entry(isin, entry_date, entry_price, fund_name)
    if ok:
        return jsonify({'status': 'ok', 'isin': isin, 'message': 'Fondo aggiunto al portafoglio'})
    else:
        return jsonify({'error': 'Errore salvataggio - database non disponibile'}), 503


@app.route('/api/portfolio/<isin>', methods=['DELETE'])
def remove_from_portfolio(isin):
    """Rimuove definitivamente un fondo dal portafoglio."""
    isin = isin.strip().upper()
    ok = db.remove_portfolio_entry(isin)
    if ok:
        return jsonify({'status': 'ok', 'isin': isin, 'message': 'Fondo rimosso dal portafoglio'})
    else:
        return jsonify({'error': 'Errore rimozione - database non disponibile'}), 503


@app.route('/api/portfolio/<isin>', methods=['PUT'])
def update_portfolio_entry(isin):
    """Modifica data/prezzo di entrata di un fondo."""
    isin = isin.strip().upper()
    data = request.get_json() or {}
    entry_date = data.get('entry_date', '')
    entry_price = data.get('entry_price')
    fund_name = data.get('fund_name')
    if not entry_date or entry_price is None:
        return jsonify({'error': 'entry_date e entry_price obbligatori'}), 400
    try:
        entry_price = float(entry_price)
    except (ValueError, TypeError):
        return jsonify({'error': 'entry_price deve essere un numero'}), 400
    ok = db.update_portfolio_entry(isin, entry_date, entry_price, fund_name)
    if ok:
        return jsonify({'status': 'ok', 'isin': isin})
    return jsonify({'error': 'Errore aggiornamento'}), 503


@app.route('/api/portfolio/<isin>/exit', methods=['POST'])
def exit_portfolio(isin):
    """Registra l'uscita (vendita) da un fondo del portafoglio."""
    isin = isin.strip().upper()
    data = request.get_json() or {}
    exit_date = data.get('exit_date', '')
    exit_price = data.get('exit_price')
    if not exit_date or exit_price is None:
        return jsonify({'error': 'exit_date e exit_price obbligatori'}), 400
    try:
        exit_price = float(exit_price)
    except (ValueError, TypeError):
        return jsonify({'error': 'exit_price deve essere un numero'}), 400
    ok = db.exit_portfolio_entry(isin, exit_date, exit_price)
    if ok:
        db.add_portfolio_event(isin, 'exit', exit_date, exit_price)
        return jsonify({'status': 'ok', 'isin': isin})
    return jsonify({'error': 'Errore salvataggio'}), 503


@app.route('/api/portfolio/<isin>/reactivate', methods=['POST'])
def reactivate_portfolio(isin):
    """Riporta un fondo a status active (annulla uscita)."""
    isin = isin.strip().upper()
    ok = db.reactivate_portfolio_entry(isin)
    if ok:
        return jsonify({'status': 'ok', 'isin': isin})
    return jsonify({'error': 'Errore'}), 503


@app.route('/api/portfolio/<isin>/switch', methods=['POST'])
def add_switch_event(isin):
    """Registra uno switch (uscita parziale) su un fondo del portafoglio."""
    isin = isin.strip().upper()
    data = request.get_json() or {}
    event_date = data.get('event_date', '')
    event_price = data.get('event_price')
    target_isin = data.get('target_isin', '').strip().upper() or None
    target_fund_name = data.get('target_fund_name', '').strip() or None
    notes = data.get('notes', '').strip() or None
    if not event_date or event_price is None:
        return jsonify({'error': 'event_date e event_price obbligatori'}), 400
    try:
        event_price = float(event_price)
    except (ValueError, TypeError):
        return jsonify({'error': 'event_price deve essere un numero'}), 400
    eid = db.add_portfolio_event(isin, 'switch', event_date, event_price,
                                 target_isin, target_fund_name, notes)
    if eid >= 0:
        return jsonify({'status': 'ok', 'id': eid, 'isin': isin})
    return jsonify({'error': 'Errore salvataggio'}), 503


@app.route('/api/portfolio/events/<isin>', methods=['GET'])
def get_portfolio_events(isin):
    """Restituisce tutti gli eventi (switch, exit) registrati per un fondo."""
    isin = isin.strip().upper()
    events = db.get_portfolio_events(isin)
    return jsonify({'isin': isin, 'events': events})


@app.route('/api/portfolio/events/<int:event_id>', methods=['PUT'])
def update_portfolio_event(event_id):
    """Modifica un evento portafoglio (data, prezzo, destinazione)."""
    data = request.get_json() or {}
    event_date = data.get('event_date', '')
    event_price = data.get('event_price')
    target_isin = data.get('target_isin', '').strip().upper() or None
    target_fund_name = data.get('target_fund_name', '').strip() or None
    notes = data.get('notes', '').strip() or None
    if not event_date:
        return jsonify({'error': 'event_date obbligatorio'}), 400
    try:
        event_price = float(event_price) if event_price is not None else None
    except (ValueError, TypeError):
        return jsonify({'error': 'event_price deve essere un numero'}), 400
    ok = db.update_portfolio_event(event_id, event_date, event_price,
                                   target_isin, target_fund_name, notes)
    if ok:
        return jsonify({'status': 'ok', 'id': event_id})
    return jsonify({'error': 'Errore aggiornamento'}), 503


@app.route('/api/portfolio/events/<int:event_id>', methods=['DELETE'])
def delete_portfolio_event(event_id):
    """Elimina un evento portafoglio."""
    ok = db.delete_portfolio_event(event_id)
    if ok:
        return jsonify({'status': 'ok', 'id': event_id})
    return jsonify({'error': 'Errore eliminazione'}), 503


@app.route('/api/l1-tracking')
def l1_tracking_api():
    """Restituisce i fondi attualmente in L1 con dati di ingresso e performance."""
    import numpy as np
    from datetime import date as date_type

    entries = db.get_all_l1_entries()   # {isin: {entry_date, entry_price}}
    today = date_type.today()
    today_str = today.isoformat()

    # Carica nomi fondi dal JSON
    fund_names = {}
    try:
        with open('data/dashboard_data.json', 'r') as f:
            dash = json.load(f)
        for lv_funds in dash.get('levels', {}).values():
            for fund in lv_funds:
                isin = fund.get('isin') or ''
                if isin:
                    fund_names[isin] = fund.get('nome', isin)
    except Exception:
        pass

    result = []
    for isin, entry in entries.items():
        entry_date = entry['entry_date']
        entry_price = entry['entry_price']

        ed = entry_date if isinstance(entry_date, date_type) else date_type.fromisoformat(str(entry_date))
        try:
            days_in_l1 = max(1, int(np.busday_count(ed, today)) + 1)
        except Exception:
            days_in_l1 = 1

        prices_df = db.get_prices(isin, days=3)
        current_nav = float(prices_df.iloc[-1]['price']) if not prices_df.empty else None

        pct_gain = None
        if current_nav and entry_price:
            pct_gain = round((current_nav - float(entry_price)) / float(entry_price) * 100, 2)

        entry_date_str = ed.isoformat()
        delta_calendar = (today - ed).days

        result.append({
            'isin': isin,
            'fund_name': fund_names.get(isin, isin),
            'entry_date': entry_date_str,
            'entry_price': float(entry_price),
            'current_nav': current_nav,
            'pct_gain': pct_gain,
            'days_in_l1': days_in_l1,
            'is_new': delta_calendar <= 1,   # badge NUOVO per giorni 1 e 2
        })

    return jsonify({'tracking': result})


@app.route('/api/l1-exits')
def l1_exits_api():
    """Restituisce le uscite da L1 degli ultimi 30 giorni."""
    days = int(request.args.get('days', 30))
    exits = db.get_l1_exits(days=days)
    result = []
    for e in exits:
        result.append({
            'isin': e['isin'],
            'fund_name': e['fund_name'] or e['isin'],
            'exit_date': str(e['exit_date']),
            'exit_price': float(e['exit_price']) if e['exit_price'] else None,
            'exit_rule': e['exit_rule'],
            'exit_trigger': e['exit_trigger'] or '',
            'entry_date': str(e['entry_date']) if e['entry_date'] else None,
            'entry_price': float(e['entry_price']) if e['entry_price'] else None,
            'days_in_l1': e['days_in_l1'],
            'pct_gain': float(e['pct_gain']) if e['pct_gain'] is not None else None,
        })
    return jsonify({'exits': result, 'count': len(result)})


@app.route('/api/portfolio-history/<isin>')
def portfolio_history(isin):
    """
    Restituisce lo storico 30gg con indicatori calcolati per un fondo del portafoglio.
    Indicatori: NAV, %1G, %1W, MM5, MM20, RSI14, Dist%MM20
    """
    import pandas as pd

    isin = isin.strip().upper()
    days_history = int(request.args.get('days', 30))

    # Recupera 60+ giorni per avere abbastanza dati per SMA20 e RSI14
    df = db.get_prices(isin, days=days_history + 40)

    if df.empty or len(df) < 2:
        return jsonify({'isin': isin, 'history': [], 'error': 'Dati storici insufficienti'})

    df = df.sort_values('date').reset_index(drop=True)
    prices = df['price'].astype(float)

    # SMA5 e SMA20
    df['mm5'] = prices.rolling(window=5, min_periods=1).mean()
    df['mm20'] = prices.rolling(window=20, min_periods=1).mean()

    # Distanza % da MM20
    df['dist_mm20'] = (prices - df['mm20']) / df['mm20'] * 100

    # RSI14
    def calc_rsi(series, period=14):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, float('nan'))
        return 100 - (100 / (1 + rs))

    df['rsi14'] = calc_rsi(prices)

    # %1G (variazione % giornaliera)
    df['pct_1g'] = prices.pct_change() * 100

    # %1W (variazione % settimanale, 5 giorni di borsa)
    df['pct_1w'] = prices.pct_change(periods=5) * 100

    # Restituisce solo gli ultimi days_history giorni
    df_out = df.tail(days_history).copy()

    history = []
    for _, row in df_out.iterrows():
        def fmt(v, decimals=2):
            return round(float(v), decimals) if pd.notna(v) else None

        history.append({
            'date': str(row['date'].date()) if hasattr(row['date'], 'date') else str(row['date'])[:10],
            'nav': fmt(row['price'], 4),
            'pct_1g': fmt(row['pct_1g']),
            'pct_1w': fmt(row['pct_1w']),
            'mm5': fmt(row['mm5'], 4),
            'mm20': fmt(row['mm20'], 4),
            'dist_mm20': fmt(row['dist_mm20']),
            'rsi14': fmt(row['rsi14']),
        })

    return jsonify({'isin': isin, 'history': history, 'count': len(history)})


@app.route('/api/fund-detail/<isin>')
def fund_detail(isin):
    """Storico completo + tutti gli indicatori + sentiment breve/medio/lungo per un fondo."""
    import pandas as pd
    import math
    from technical_analysis import TechnicalAnalyzer

    isin = isin.strip().upper()
    days_req = int(request.args.get('days', 60))

    df = db.get_prices(isin, days=max(days_req + 110, 270))
    if df.empty or len(df) < 2:
        return jsonify({'isin': isin, 'error': 'Dati storici insufficienti'})

    df = df.sort_values('date').reset_index(drop=True)
    prices = df['price'].astype(float)

    fund_info = {}
    try:
        with open('data/dashboard_data.json', 'r') as f:
            dash = json.load(f)
        for level_key, level_funds in dash.get('levels', {}).items():
            for fund in level_funds:
                if (fund.get('isin') or fund.get('ISIN') or '') == isin:
                    fund_info = {**fund, 'livello': int(level_key)}
                    break
            if fund_info:
                break
        if not fund_info:
            for fund in dash.get('l0_funds', []):
                if (fund.get('isin') or '') == isin:
                    fund_info = {**fund, 'livello': 0}
                    break
    except Exception:
        pass

    asset_type = fund_info.get('asset_type') or TechnicalAnalyzer.detect_asset_type(fund_info.get('categoria', ''))
    analyzer = TechnicalAnalyzer(asset_type=asset_type)

    ma5  = prices.rolling(window=5,  min_periods=1).mean()
    ma20 = prices.rolling(window=20, min_periods=1).mean()
    ma50 = prices.rolling(window=50, min_periods=1).mean()

    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, min_periods=14).mean()
    rsi14 = 100 - (100 / (1 + avg_gain / avg_loss.replace(0, float('nan'))))

    adx_s = analyzer.calculate_adx(prices)
    ema12 = prices.ewm(span=12).mean()
    ema26 = prices.ewm(span=26).mean()
    macd_line   = ema12 - ema26
    macd_signal = macd_line.ewm(span=9).mean()
    macd_hist   = macd_line - macd_signal
    bb = analyzer.calculate_bollinger_bands(prices)

    df['mm5']         = ma5
    df['mm20']        = ma20
    df['mm50']        = ma50
    df['rsi14']       = rsi14
    df['adx']         = adx_s
    df['macd_hist']   = macd_hist
    df['bb_upper']    = bb['upper']
    df['bb_lower']    = bb['lower']
    df['dist_mm20']   = (prices - ma20) / ma20 * 100
    df['pct_1g']      = prices.pct_change() * 100
    df['pct_1w']      = prices.pct_change(periods=5) * 100
    df['pct_1m']      = prices.pct_change(periods=22) * 100

    def fmt(v, d=2):
        try:
            f = float(v)
            return None if math.isnan(f) or math.isinf(f) else round(f, d)
        except Exception:
            return None

    df_out = df.tail(days_req).copy()
    history = []
    for _, row in df_out.iterrows():
        history.append({
            'date':       str(row['date'].date()) if hasattr(row['date'], 'date') else str(row['date'])[:10],
            'nav':        fmt(row['price'], 4),
            'pct_1g':     fmt(row['pct_1g']),
            'pct_1w':     fmt(row['pct_1w']),
            'pct_1m':     fmt(row['pct_1m']),
            'mm5':        fmt(row['mm5'], 4),
            'mm20':       fmt(row['mm20'], 4),
            'mm50':       fmt(row['mm50'], 4) if len(prices) >= 50 else None,
            'dist_mm20':  fmt(row['dist_mm20']),
            'rsi14':      fmt(row['rsi14'], 1),
            'adx':        fmt(row['adx'], 1),
            'macd_hist':  fmt(row['macd_hist'], 4),
            'bb_upper':   fmt(row['bb_upper'], 4),
            'bb_lower':   fmt(row['bb_lower'], 4),
        })

    # ── Sentiment ─────────────────────────────────────────────────────────────
    last = history[-1] if history else {}
    rsi   = last.get('rsi14') or 50.0
    adx_v = last.get('adx')   or 0.0
    mh    = last.get('macd_hist') or 0.0
    dist  = last.get('dist_mm20') or 0.0
    pct1w = last.get('pct_1w') or 0.0
    pct1m = last.get('pct_1m') or 0.0
    nav_v = last.get('nav')  or 0.0
    mm20_v= last.get('mm20') or 0.0
    mm50_v= last.get('mm50') or 0.0

    max_52w   = float(prices.tail(252).max()) if len(prices) >= 252 else float(prices.max())
    dist_52w  = round((nav_v - max_52w) / max_52w * 100, 2) if max_52w else 0.0

    def verdict(score):
        if score >= 2:  return 'COMPRA'
        if score <= -2: return 'VENDI'
        return 'MANTIENI'

    def vcolor(v):
        return 'green' if v == 'COMPRA' else 'red' if v == 'VENDI' else 'yellow'

    # Breve termine
    ss, sf = 0, []
    if rsi < 35:   ss += 2; sf.append(f'RSI {rsi:.0f} — ipervenduto, possibile rimbalzo')
    elif rsi < 45: ss += 1; sf.append(f'RSI {rsi:.0f} — debolezza, attenzione')
    elif rsi > 75: ss -= 2; sf.append(f'RSI {rsi:.0f} — ipercomprato, rischio correzione')
    elif rsi > 65: ss -= 1; sf.append(f'RSI {rsi:.0f} — zona calda')
    else:                    sf.append(f'RSI {rsi:.0f} — zona neutrale')
    if mh > 0:   ss += 1; sf.append('MACD istogramma positivo — momentum rialzista')
    elif mh < 0: ss -= 1; sf.append('MACD istogramma negativo — momentum ribassista')
    if pct1w > 1:    ss += 1; sf.append(f'%1W +{pct1w:.1f}% — settimana positiva')
    elif pct1w < -1: ss -= 1; sf.append(f'%1W {pct1w:.1f}% — settimana negativa')

    # Medio termine
    ms, mf = 0, []
    if mm20_v and nav_v:
        if nav_v > mm20_v: ms += 1; mf.append(f'NAV sopra MM20 ({dist:+.1f}%)')
        else:              ms -= 1; mf.append(f'NAV sotto MM20 ({dist:+.1f}%)')
    if adx_v > 25:   ms += 1; mf.append(f'ADX {adx_v:.0f} — trend strutturato')
    elif adx_v < 15:          mf.append(f'ADX {adx_v:.0f} — mercato laterale')
    if pct1m > 3:    ms += 1; mf.append(f'%1M +{pct1m:.1f}% — trend mensile positivo')
    elif pct1m < -3: ms -= 1; mf.append(f'%1M {pct1m:.1f}% — trend mensile negativo')
    if 45 <= rsi <= 65: ms += 1; mf.append(f'RSI {rsi:.0f} in zona di salute (45–65)')

    # Lungo termine
    ls, lf = 0, []
    if mm50_v and nav_v:
        if nav_v > mm50_v: ls += 1; lf.append('NAV sopra MM50 — trend a lungo positivo')
        else:              ls -= 1; lf.append('NAV sotto MM50 — trend a lungo negativo')
    if mm20_v and mm50_v:
        if mm20_v > mm50_v: ls += 1; lf.append('MM20 > MM50 — allineamento rialzista')
        else:               ls -= 1; lf.append('MM20 < MM50 — allineamento ribassista')
    if dist_52w > -10:   ls += 1; lf.append(f'NAV a {dist_52w:.1f}% dal max 52W — forza strutturale')
    elif dist_52w < -25: ls -= 1; lf.append(f'NAV a {dist_52w:.1f}% dal max 52W — debolezza strutturale')
    else:                          lf.append(f'NAV a {dist_52w:.1f}% dal max 52W')
    if adx_v > 30: ls += 1; lf.append(f'ADX {adx_v:.0f} — trend forte e sostenuto')

    sv, mv, lv = verdict(ss), verdict(ms), verdict(ls)

    # ── Portfolio Analysis (parametri opzionali) ──────────────────────────────
    portfolio_analysis = None
    ep_str = request.args.get('entry_price')
    ed_str = request.args.get('entry_date')
    if ep_str and ed_str:
        try:
            from datetime import date as date_type
            ep = float(ep_str)
            nav_curr = float(nav_v) if nav_v else 0.0

            # P&L e durata
            pnl_pct = round((nav_curr - ep) / ep * 100, 2) if ep > 0 else 0.0
            try:
                entry_dt = datetime.strptime(ed_str, '%Y-%m-%d').date()
                days_held = (date_type.today() - entry_dt).days
            except Exception:
                days_held = 0

            # Rendimento annualizzato
            ann_ret = None
            if days_held > 7 and ep > 0:
                ann_ret = round(((nav_curr / ep) ** (365.0 / days_held) - 1) * 100, 2)

            # Volatilità 30gg e media variazione giornaliera
            pct_30 = df['pct_1g'].dropna().tail(30)
            vol_30d   = round(float(pct_30.std()),  3) if len(pct_30) > 1 else None
            avg_daily = round(float(pct_30.mean()), 3) if len(pct_30) > 1 else None

            # Max Drawdown dall'acquisto
            date_col = df['date'].apply(lambda x: str(x.date() if hasattr(x, 'date') else x)[:10])
            since_mask   = date_col >= ed_str
            since_prices = df.loc[since_mask, 'price'].astype(float)
            max_dd = None
            if len(since_prices) > 1:
                peak, worst = float(since_prices.iloc[0]), 0.0
                for p in since_prices:
                    p = float(p)
                    if p > peak: peak = p
                    dd = (p - peak) / peak * 100
                    if dd < worst: worst = dd
                max_dd = round(worst, 2)

            # Analisi entrata: trova i valori del giorno di acquisto nello storico COMPLETO
            entry_rsi = entry_dist = entry_nav_actual = None
            all_hist_df = df.copy()
            for _, row in all_hist_df.iterrows():
                d_str = str(row['date'].date() if hasattr(row['date'], 'date') else row['date'])[:10]
                if d_str >= ed_str:
                    entry_rsi   = fmt(row['rsi14'], 1)
                    entry_dist  = fmt(row['dist_mm20'], 2)
                    entry_nav_actual = fmt(row['price'], 4)
                    break

            portfolio_analysis = {
                'entry_price':       ep,
                'entry_date':        ed_str,
                'entry_nav_actual':  entry_nav_actual,
                'pnl_pct':           pnl_pct,
                'days_held':         days_held,
                'annualized_return': ann_ret,
                'volatility_30d':    vol_30d,
                'avg_daily_change':  avg_daily,
                'max_drawdown_entry': max_dd,
                'entry_rsi':         entry_rsi,
                'entry_dist_mm20':   entry_dist,
                'stop_mm20':         fmt(float(mm20_v), 4) if mm20_v else None,
                'stop_5pct':         fmt(ep * 0.95, 4),
                'target_5pct':       fmt(ep * 1.05, 4),
                'target_10pct':      fmt(ep * 1.10, 4),
                'target_15pct':      fmt(ep * 1.15, 4),
            }
        except Exception as exc:
            portfolio_analysis = {'error': str(exc)}

    return jsonify({
        'isin':         isin,
        'fund_name':    fund_info.get('nome') or isin,
        'asset_type':   asset_type,
        'level':        fund_info.get('livello'),
        'signal_purity': fund_info.get('signal_purity'),
        'history':      history,
        'count':        len(history),
        'dist_52w':     dist_52w,
        'current': {
            'nav':       fmt(nav_v, 4),
            'rsi14':     fmt(rsi, 1),
            'adx':       fmt(adx_v, 1),
            'mm20':      fmt(mm20_v, 4),
            'mm50':      fmt(mm50_v, 4) if mm50_v else None,
            'dist_mm20': fmt(dist, 1),
            'macd_hist': fmt(mh, 4),
            'dist_52w':  dist_52w,
        },
        'sentiment': {
            'short': {'verdict': sv, 'score': ss, 'label': 'Breve termine (1–2 sett.)', 'factors': sf, 'color': vcolor(sv)},
            'mid':   {'verdict': mv, 'score': ms, 'label': 'Medio termine (1–3 mesi)',  'factors': mf, 'color': vcolor(mv)},
            'long':  {'verdict': lv, 'score': ls, 'label': 'Lungo termine (6–12 mesi)', 'factors': lf, 'color': vcolor(lv)},
        },
        'portfolio_analysis': portfolio_analysis,
    })


if __name__ == '__main__':
    # Crea cartella data se non esiste
    os.makedirs('data', exist_ok=True)

    # Crea file dati iniziale se non esiste
    if not os.path.exists('data/dashboard_data.json'):
        initial_data = {
            'last_update': datetime.now().isoformat(),
            'summary': {'total_funds': 0, 'buy_signals': 0, 'sell_signals': 0, 'hold_signals': 0},
            'levels': {'1': [], '2': [], '3': []},
            'categories': {}
        }
        with open('data/dashboard_data.json', 'w') as f:
            json.dump(initial_data, f)

    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

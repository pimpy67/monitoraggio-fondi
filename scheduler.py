"""
scheduler.py - Scheduler per monitoraggio automatico
=====================================================
Run principale (con alert email): MONITOR_HOUR:MONITOR_MINUTE UTC
  default → 16:00 UTC = 18:00 CEST — quando tutti i NAV del giorno prima sono pubblicati

Run mattutino silenzioso (solo aggiorna dashboard, nessun alert):
  MONITOR_HOUR_SOFT:MONITOR_MINUTE_SOFT UTC, opzionale
  default → 07:00 UTC = 09:00 CEST — cattura fondi che pubblicano il NAV al mattino

Fallback: ogni 30 minuti verifica se il run principale è già avvenuto oggi.
"""

import schedule
import time
import os
import json
from datetime import datetime
import threading

# Carica .env con override=True così i valori nel file .env battono le variabili
# d'ambiente iniettate da Docker — permette di cambiare orari senza rebuild immagine
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

from monitor import FundMonitor
import monitor_lock


def _has_full_run_today():
    """Controlla se il run principale (con alert) ha già girato oggi con successo."""
    try:
        with open('data/dashboard_data.json', 'r') as f:
            data = json.load(f)
        last_update = data.get('last_update')
        total_funds = data.get('summary', {}).get('total_funds', 0)
        alerts_sent = data.get('summary', {}).get('alerts_sent', True)

        if total_funds == 0:
            return False
        if last_update:
            last_dt = datetime.fromisoformat(last_update)
            return last_dt.date() == datetime.now().date() and alerts_sent
    except Exception:
        pass
    return False


def run_monitor(send_alerts: bool = True):
    """Esegue il monitoraggio (con lock condiviso per evitare esecuzioni parallele).
    send_alerts=True → run completo con email.
    send_alerts=False → refresh silenzioso, solo aggiorna dashboard.
    """
    if not monitor_lock.try_acquire():
        print(f"⚠️ Scheduler: monitoraggio già in esecuzione, skip")
        return

    def _do_monitor():
        try:
            label = "completo" if send_alerts else "silenzioso"
            print(f"\n⏰ Scheduler: avvio run {label} - {datetime.now()}")
            monitor = FundMonitor()
            monitor.run(send_daily_report=send_alerts)
        except Exception as e:
            print(f"❌ Errore durante monitoraggio: {e}")
            import traceback
            traceback.print_exc()
        finally:
            monitor_lock.release()

    t = threading.Thread(target=_do_monitor, daemon=True)
    t.start()
    t.join(timeout=600)  # Max 10 minuti
    if t.is_alive():
        print(f"⚠️ Scheduler: monitoraggio ancora in corso dopo 10 min, rilascio lock")
        monitor_lock.release()


def fallback_check():
    """Controllo fallback: se il run principale non ha girato oggi, lancialo.
    Si attiva solo dopo MONITOR_HOUR per evitare run prematuri al mattino."""
    hour_main = int(os.environ.get('MONITOR_HOUR', 16))
    now = datetime.now()

    if now.hour >= hour_main and not _has_full_run_today() and not monitor_lock.is_running():
        print(f"\n🔄 Scheduler FALLBACK: run principale non eseguito oggi, lancio ora...")
        run_monitor(send_alerts=True)


def _schedule_day(num, schedule_time, job_fn):
    """Aggiunge un job per il giorno della settimana indicato (1=lun, 7=dom)."""
    if num == 1:
        schedule.every().monday.at(schedule_time).do(job_fn)
    elif num == 2:
        schedule.every().tuesday.at(schedule_time).do(job_fn)
    elif num == 3:
        schedule.every().wednesday.at(schedule_time).do(job_fn)
    elif num == 4:
        schedule.every().thursday.at(schedule_time).do(job_fn)
    elif num == 5:
        schedule.every().friday.at(schedule_time).do(job_fn)
    elif num == 6:
        schedule.every().saturday.at(schedule_time).do(job_fn)
    elif num == 7:
        schedule.every().sunday.at(schedule_time).do(job_fn)


def run_scheduler():
    """Avvia lo scheduler con run principale + run mattutino opzionale + fallback."""
    # Run principale (con alert)
    hour_main   = int(os.environ.get('MONITOR_HOUR', 16))
    minute_main = int(os.environ.get('MONITOR_MINUTE', 0))
    time_main   = f"{hour_main:02d}:{minute_main:02d}"

    # Run mattutino silenzioso (opzionale)
    hour_soft_env = os.environ.get('MONITOR_HOUR_SOFT')
    hour_soft     = int(hour_soft_env) if hour_soft_env else None
    minute_soft   = int(os.environ.get('MONITOR_MINUTE_SOFT', 0))
    time_soft     = f"{hour_soft:02d}:{minute_soft:02d}" if hour_soft is not None else None

    # Giorni della settimana
    days_spec = os.environ.get('MONITOR_DAYS', '1-5')
    day_nums = []
    try:
        if ',' in days_spec:
            day_nums = [int(p.strip()) for p in days_spec.split(',')]
        elif '-' in days_spec:
            a, b = days_spec.split('-')
            day_nums = list(range(int(a.strip()), int(b.strip()) + 1))
        else:
            day_nums = [int(days_spec.strip())]
    except Exception:
        day_nums = [1, 2, 3, 4, 5]

    # Registra job principale
    for d in sorted(set(day_nums)):
        _schedule_day(d, time_main, lambda: run_monitor(send_alerts=True))

    print(f"📅 Run principale: {time_main} UTC  ({hour_main + 2:02d}:{minute_main:02d} CEST) — lun-ven con alert email")

    # Registra run mattutino silenzioso (se configurato)
    if time_soft:
        for d in sorted(set(day_nums)):
            _schedule_day(d, time_soft, lambda: run_monitor(send_alerts=False))
        print(f"🌅 Run silenzioso:  {time_soft} UTC  ({hour_soft + 2:02d}:{minute_soft:02d} CEST) — solo refresh dashboard")

    # Fallback ogni 30 minuti
    schedule.every(30).minutes.do(fallback_check)
    print(f"🔄 Fallback attivo: controllo ogni 30 minuti (si attiva dopo {time_main} se run non eseguito)")

    now = datetime.now()
    prossimo = "oggi" if now.hour < hour_main else "domani"
    print(f"   Prossima esecuzione principale: {prossimo} alle {time_main} UTC")

    loop_count = 0
    while True:
        try:
            schedule.run_pending()
            loop_count += 1
            if loop_count % 60 == 0:
                print(f"💓 Scheduler heartbeat: {now.strftime('%Y-%m-%d %H:%M')} - "
                      f"prossimo job: {schedule.next_run()}")
        except Exception as e:
            print(f"⚠️ Scheduler errore nel loop: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(60)


def start_scheduler_thread():
    """Avvia scheduler in un thread separato (per uso con Flask)."""
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    return scheduler_thread


if __name__ == "__main__":
    print("=" * 50)
    print("🚀 FUND MONITOR SCHEDULER")
    print("=" * 50)

    if os.environ.get('RUN_ON_START', 'false').lower() == 'true':
        print("\n▶ Esecuzione monitoraggio iniziale...")
        run_monitor(send_alerts=True)

    run_scheduler()

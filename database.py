"""
database.py - Gestione database PostgreSQL per storico prezzi
==============================================================
Salva e recupera lo storico dei prezzi dei fondi su PostgreSQL (Railway).
Include retry automatico, auto-detect URL interno e resilienza.
"""

import os
import time
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import logging

# Usa psycopg2 per PostgreSQL
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    logging.warning("psycopg2 non installato. Installa con: pip install psycopg2-binary")


# Numero massimo di tentativi per connessione
MAX_RETRIES = 3
RETRY_DELAY = 2  # secondi tra un tentativo e l'altro


class PriceDatabase:
    """Gestisce lo storico prezzi su PostgreSQL con retry e resilienza"""

    def __init__(self, database_url: str = None):
        self.database_url = database_url or self._detect_database_url()
        self._db_available = False

        if not POSTGRES_AVAILABLE:
            print("psycopg2 non disponibile - installa con: pip install psycopg2-binary")
            return

        if not self.database_url:
            print("DATABASE_URL non trovato. Lo storico non verra' salvato su PostgreSQL.")
            return

        # Mostra URL mascherato
        safe_url = self.database_url.split('@')[-1] if '@' in self.database_url else '***'
        print(f"DATABASE_URL configurato: ...@{safe_url}")

        # Inizializza la tabella (con retry)
        self._init_table()

    @staticmethod
    def _detect_database_url() -> Optional[str]:
        """
        Cerca l'URL del database.
        Ordine: DATABASE_URL > DATABASE_PUBLIC_URL > costruito da variabili PG*
        """
        # 1. DATABASE_URL (impostato manualmente o da Railway)
        url = os.environ.get('DATABASE_URL')
        if url:
            safe = url.split('@')[-1] if '@' in url else '***'
            print(f"Usando DATABASE_URL: ...@{safe}")
            return url

        # 2. DATABASE_PUBLIC_URL (Railway proxy pubblico)
        url = os.environ.get('DATABASE_PUBLIC_URL')
        if url:
            safe = url.split('@')[-1] if '@' in url else '***'
            print(f"Usando DATABASE_PUBLIC_URL: ...@{safe}")
            return url

        # 3. Costruisci da variabili PG*
        pghost = os.environ.get('PGHOST')
        pguser = os.environ.get('PGUSER', 'postgres')
        pgpassword = os.environ.get('PGPASSWORD')
        pgdatabase = os.environ.get('PGDATABASE', 'railway')
        pgport = os.environ.get('PGPORT', '5432')

        if pghost and pgpassword:
            url = f"postgresql://{pguser}:{pgpassword}@{pghost}:{pgport}/{pgdatabase}"
            print(f"DATABASE_URL costruito da PG*: {pghost}:{pgport}")
            return url

        print("Nessuna variabile database trovata (DATABASE_URL, DATABASE_PUBLIC_URL, PGHOST)")
        return None

    def _get_connection(self, retries: int = MAX_RETRIES):
        """Ottiene una connessione al database con retry automatico"""
        if not self.database_url or not POSTGRES_AVAILABLE:
            return None

        last_error = None
        for attempt in range(1, retries + 1):
            # Prova con SSL (richiesto dai proxy pubblici Railway)
            try:
                conn = psycopg2.connect(self.database_url, sslmode='require', connect_timeout=10)
                if attempt > 1:
                    print(f"  Connessione riuscita al tentativo {attempt}")
                self._db_available = True
                return conn
            except Exception as e:
                last_error = e

            # Prova senza SSL (funziona con URL interni Railway)
            try:
                conn = psycopg2.connect(self.database_url, connect_timeout=10)
                if attempt > 1:
                    print(f"  Connessione (no-SSL) riuscita al tentativo {attempt}")
                self._db_available = True
                return conn
            except Exception as e:
                last_error = e

            if attempt < retries:
                wait = RETRY_DELAY * attempt
                print(f"  Connessione fallita (tentativo {attempt}/{retries}), riprovo tra {wait}s...")
                time.sleep(wait)

        print(f"Connessione database fallita dopo {retries} tentativi: {last_error}")
        self._db_available = False
        return None

    def is_available(self) -> bool:
        """Controlla se il database e' raggiungibile"""
        conn = self._get_connection(retries=1)
        if conn:
            conn.close()
            return True
        return False

    def _init_table(self):
        """Crea le tabelle necessarie se non esistono"""
        conn = self._get_connection()
        if not conn:
            print("Impossibile inizializzare tabella - database non raggiungibile")
            return

        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS price_history (
                        id SERIAL PRIMARY KEY,
                        isin VARCHAR(20) NOT NULL,
                        date DATE NOT NULL,
                        price DECIMAL(12, 4) NOT NULL,
                        source VARCHAR(50),
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(isin, date)
                    )
                """)
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint
                            WHERE conname = 'price_history_isin_date_key'
                        ) THEN
                            ALTER TABLE price_history ADD CONSTRAINT price_history_isin_date_key UNIQUE (isin, date);
                        END IF;
                    END $$;
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_price_history_isin_date
                    ON price_history(isin, date DESC)
                """)
                # Tabella per tracciare l'ingresso dei fondi in Livello 1
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS l1_tracking (
                        isin VARCHAR(20) PRIMARY KEY,
                        entry_date DATE NOT NULL,
                        entry_price DECIMAL(12, 4) NOT NULL
                    )
                """)
                # Tabella per tracciare l'ingresso dei fondi in Livello 0 (Deep Recovery)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS l0_tracking (
                        isin VARCHAR(20) PRIMARY KEY,
                        entry_date DATE NOT NULL,
                        entry_price DECIMAL(12, 4) NOT NULL,
                        panic_low DECIMAL(12, 4) NOT NULL
                    )
                """)
                # Tabella per il portafoglio personale dell'utente
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS portfolio_entries (
                        isin VARCHAR(20) PRIMARY KEY,
                        entry_date DATE NOT NULL,
                        entry_price DECIMAL(12, 4) NOT NULL,
                        fund_name VARCHAR(200),
                        added_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                # Storico uscite da L1 (fondi che hanno lasciato il livello 1)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS l1_exit_history (
                        id SERIAL PRIMARY KEY,
                        isin VARCHAR(20) NOT NULL,
                        fund_name VARCHAR(200),
                        exit_date DATE NOT NULL,
                        exit_price DECIMAL(12, 4),
                        exit_rule INTEGER,
                        exit_trigger TEXT,
                        entry_date DATE,
                        entry_price DECIMAL(12, 4),
                        days_in_l1 INTEGER,
                        pct_gain DECIMAL(8, 4),
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_l1_exit_date ON l1_exit_history(exit_date DESC)")
                conn.commit()
                print("Tabelle price_history, l1_tracking, l0_tracking, portfolio_entries, l1_exit_history pronte")
        except Exception as e:
            logging.error(f"Errore creazione tabella: {e}")
        finally:
            conn.close()

    def save_price(self, isin: str, date: str, price: float, source: str = 'FT Markets') -> bool:
        """Salva un prezzo nel database (con retry)"""
        conn = self._get_connection()
        if not conn:
            print(f"DB non disponibile - prezzo {isin} non salvato")
            return False

        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO price_history (isin, date, price, source, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (isin, date)
                    DO UPDATE SET price = EXCLUDED.price, source = EXCLUDED.source, updated_at = NOW()
                """, (isin, date, price, source))
                conn.commit()
                print(f"  Prezzo salvato in DB: {isin} = {price} ({date})")
                return True
        except Exception as e:
            print(f"Errore salvataggio prezzo {isin}: {e}")
            return False
        finally:
            conn.close()

    def get_prices(self, isin: str, days: int = 30) -> pd.DataFrame:
        """Recupera lo storico prezzi per un fondo"""
        conn = self._get_connection()
        if not conn:
            return pd.DataFrame(columns=['date', 'price'])

        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT date, price
                    FROM price_history
                    WHERE isin = %s
                    ORDER BY date DESC
                    LIMIT %s
                """, (isin, days))
                rows = cur.fetchall()

                if rows:
                    df = pd.DataFrame(rows)
                    df['date'] = pd.to_datetime(df['date'])
                    df['price'] = df['price'].astype(float)
                    df = df.sort_values('date').reset_index(drop=True)
                    return df
                return pd.DataFrame(columns=['date', 'price'])
        except Exception as e:
            logging.error(f"Errore recupero prezzi {isin}: {e}")
            return pd.DataFrame(columns=['date', 'price'])
        finally:
            conn.close()

    def get_price_series(self, isin: str, days: int = 30) -> pd.Series:
        """Recupera lo storico prezzi come Serie pandas"""
        df = self.get_prices(isin, days)
        if df.empty:
            return pd.Series(dtype=float)
        return pd.Series(df['price'].values, index=df['date'])

    def get_yesterday_price(self, isin: str) -> Optional[float]:
        """Recupera il prezzo di ieri per un fondo"""
        conn = self._get_connection()
        if not conn:
            return None

        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT price FROM price_history
                    WHERE isin = %s AND date = CURRENT_DATE - INTERVAL '1 day'
                """, (isin,))
                row = cur.fetchone()
                return float(row[0]) if row else None
        except Exception as e:
            logging.error(f"Errore recupero prezzo ieri {isin}: {e}")
            return None
        finally:
            conn.close()

    def get_last_price_date(self, isin: str) -> Optional[str]:
        """Recupera la data dell'ultimo prezzo salvato per un dato ISIN"""
        conn = self._get_connection()
        if not conn:
            return None

        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT MAX(date) as last_date FROM price_history WHERE isin = %s
                """, (isin,))
                row = cur.fetchone()
                return str(row['last_date']) if row and row['last_date'] else None
        except Exception as e:
            logging.error(f"Errore recupero ultima data prezzo per {isin}: {e}")
            return None
        finally:
            conn.close()

    def get_all_prices(self) -> pd.DataFrame:
        """Recupera tutti i prezzi nel database"""
        conn = self._get_connection()
        if not conn:
            return pd.DataFrame()

        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT isin, date, price, source, created_at, updated_at
                    FROM price_history
                    ORDER BY isin, date DESC
                """)
                rows = cur.fetchall()
                return pd.DataFrame(rows) if rows else pd.DataFrame()
        except Exception as e:
            logging.error(f"Errore recupero tutti i prezzi: {e}")
            return pd.DataFrame()
        finally:
            conn.close()

    def count_prices(self, isin: str = None) -> int:
        """Conta i prezzi salvati"""
        conn = self._get_connection()
        if not conn:
            return 0

        try:
            with conn.cursor() as cur:
                if isin:
                    cur.execute("SELECT COUNT(*) FROM price_history WHERE isin = %s", (isin,))
                else:
                    cur.execute("SELECT COUNT(*) FROM price_history")
                return cur.fetchone()[0]
        except Exception as e:
            logging.error(f"Errore conteggio prezzi: {e}")
            return 0
        finally:
            conn.close()

    def get_stats(self) -> Dict:
        """Statistiche sul database"""
        conn = self._get_connection()
        if not conn:
            return {'error': 'Database non disponibile'}

        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT COUNT(*) as total FROM price_history")
                total = cur.fetchone()['total']

                cur.execute("SELECT COUNT(DISTINCT isin) as funds FROM price_history")
                funds = cur.fetchone()['funds']

                cur.execute("""
                    SELECT MIN(date) as first_date, MAX(date) as last_date
                    FROM price_history
                """)
                dates = cur.fetchone()

                cur.execute("""
                    SELECT isin, COUNT(*) as count
                    FROM price_history
                    GROUP BY isin
                    ORDER BY count DESC
                """)
                by_fund = cur.fetchall()

                return {
                    'total_records': total,
                    'unique_funds': funds,
                    'first_date': str(dates['first_date']) if dates['first_date'] else None,
                    'last_date': str(dates['last_date']) if dates['last_date'] else None,
                    'records_by_fund': {r['isin']: r['count'] for r in by_fund}
                }
        except Exception as e:
            logging.error(f"Errore statistiche: {e}")
            return {'error': str(e)}
        finally:
            conn.close()


    # ── L1 Tracking ──────────────────────────────────────────────────────────

    def get_all_l1_entries(self) -> Dict[str, Dict]:
        """
        Restituisce tutti i fondi attualmente tracciati in L1.

        Returns:
            Dict {isin: {entry_date: date, entry_price: float}}
        """
        conn = self._get_connection()
        if not conn:
            return {}
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT isin, entry_date, entry_price FROM l1_tracking")
                rows = cur.fetchall()
                return {
                    r['isin']: {
                        'entry_date': r['entry_date'],
                        'entry_price': float(r['entry_price'])
                    }
                    for r in rows
                }
        except Exception as e:
            logging.error(f"Errore get_all_l1_entries: {e}")
            return {}
        finally:
            conn.close()

    def set_l1_entry(self, isin: str, entry_date: str, entry_price: float) -> bool:
        """
        Registra l'ingresso di un fondo in L1 (INSERT, non sovrascrive se già presente).

        Args:
            isin: Codice ISIN
            entry_date: Data ingresso 'YYYY-MM-DD'
            entry_price: Prezzo al momento dell'ingresso
        """
        conn = self._get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO l1_tracking (isin, entry_date, entry_price)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (isin) DO NOTHING
                """, (isin, entry_date, float(entry_price)))
                conn.commit()
                return True
        except Exception as e:
            logging.error(f"Errore set_l1_entry {isin}: {e}")
            return False
        finally:
            conn.close()

    def remove_l1_entry(self, isin: str) -> bool:
        """Rimuove un fondo dal tracking L1 (uscita da L1)."""
        conn = self._get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM l1_tracking WHERE isin = %s", (isin,))
                conn.commit()
                return True
        except Exception as e:
            logging.error(f"Errore remove_l1_entry {isin}: {e}")
            return False
        finally:
            conn.close()

    def save_l1_exit(self, isin: str, fund_name: str, exit_date: str, exit_price,
                     exit_rule, exit_trigger: str, entry_date, entry_price,
                     days_in_l1: int, pct_gain) -> bool:
        """Registra l'uscita di un fondo da L1 nello storico."""
        conn = self._get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO l1_exit_history
                        (isin, fund_name, exit_date, exit_price, exit_rule, exit_trigger,
                         entry_date, entry_price, days_in_l1, pct_gain)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (isin, fund_name,
                      exit_date,
                      float(exit_price) if exit_price else None,
                      exit_rule,
                      exit_trigger,
                      str(entry_date) if entry_date else None,
                      float(entry_price) if entry_price else None,
                      days_in_l1,
                      float(pct_gain) if pct_gain is not None else None))
                conn.commit()
                return True
        except Exception as e:
            logging.error(f"Errore save_l1_exit {isin}: {e}")
            return False
        finally:
            conn.close()

    def get_l1_exits(self, days: int = 30) -> list:
        """Restituisce le ultime N giorni di uscite da L1."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT isin, fund_name, exit_date, exit_price, exit_rule, exit_trigger,
                           entry_date, entry_price, days_in_l1, pct_gain
                    FROM l1_exit_history
                    WHERE exit_date >= CURRENT_DATE - INTERVAL '%s days'
                    ORDER BY exit_date DESC
                """, (days,))
                rows = cur.fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logging.error(f"Errore get_l1_exits: {e}")
            return []
        finally:
            conn.close()

    # ── L0 Tracking ──────────────────────────────────────────────────────────

    def get_all_l0_entries(self) -> Dict[str, Dict]:
        """
        Restituisce tutti i fondi attualmente tracciati in L0.

        Returns:
            Dict {isin: {entry_date, entry_price, panic_low}}
        """
        conn = self._get_connection()
        if not conn:
            return {}
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT isin, entry_date, entry_price, panic_low FROM l0_tracking")
                rows = cur.fetchall()
                return {
                    r['isin']: {
                        'entry_date': r['entry_date'],
                        'entry_price': float(r['entry_price']),
                        'panic_low': float(r['panic_low'])
                    }
                    for r in rows
                }
        except Exception as e:
            logging.error(f"Errore get_all_l0_entries: {e}")
            return {}
        finally:
            conn.close()

    def set_l0_entry(self, isin: str, entry_date: str, entry_price: float, panic_low: float) -> bool:
        """
        Registra l'ingresso di un fondo in L0 (INSERT, non sovrascrive se già presente).

        Args:
            isin: Codice ISIN
            entry_date: Data ingresso 'YYYY-MM-DD'
            entry_price: Prezzo al momento dell'ingresso
            panic_low: Minimo del panic move (stop loss assoluto)
        """
        conn = self._get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO l0_tracking (isin, entry_date, entry_price, panic_low)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (isin) DO NOTHING
                """, (isin, entry_date, float(entry_price), float(panic_low)))
                conn.commit()
                return True
        except Exception as e:
            logging.error(f"Errore set_l0_entry {isin}: {e}")
            return False
        finally:
            conn.close()

    def remove_l0_entry(self, isin: str) -> bool:
        """Rimuove un fondo dal tracking L0 (uscita da L0)."""
        conn = self._get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM l0_tracking WHERE isin = %s", (isin,))
                conn.commit()
                return True
        except Exception as e:
            logging.error(f"Errore remove_l0_entry {isin}: {e}")
            return False
        finally:
            conn.close()


    # ── Portfolio Personale ───────────────────────────────────────────────────

    def get_portfolio_entries(self) -> List[Dict]:
        """
        Restituisce tutti i fondi nel portafoglio personale.

        Returns:
            Lista di dict {isin, entry_date, entry_price, fund_name, added_at}
        """
        conn = self._get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT isin, entry_date, entry_price, fund_name, added_at
                    FROM portfolio_entries
                    ORDER BY added_at DESC
                """)
                rows = cur.fetchall()
                return [
                    {
                        'isin': r['isin'],
                        'entry_date': str(r['entry_date']),
                        'entry_price': float(r['entry_price']),
                        'fund_name': r['fund_name'] or '',
                        'added_at': str(r['added_at'])
                    }
                    for r in rows
                ]
        except Exception as e:
            logging.error(f"Errore get_portfolio_entries: {e}")
            return []
        finally:
            conn.close()

    def add_portfolio_entry(self, isin: str, entry_date: str, entry_price: float,
                            fund_name: str = '') -> bool:
        """
        Aggiunge o aggiorna un fondo nel portafoglio personale.

        Args:
            isin: Codice ISIN
            entry_date: Data di acquisto 'YYYY-MM-DD'
            entry_price: Prezzo di acquisto (NAV)
            fund_name: Nome del fondo (opzionale)
        """
        conn = self._get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO portfolio_entries (isin, entry_date, entry_price, fund_name, added_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (isin)
                    DO UPDATE SET
                        entry_date = EXCLUDED.entry_date,
                        entry_price = EXCLUDED.entry_price,
                        fund_name = EXCLUDED.fund_name,
                        added_at = NOW()
                """, (isin, entry_date, float(entry_price), fund_name))
                conn.commit()
                return True
        except Exception as e:
            logging.error(f"Errore add_portfolio_entry {isin}: {e}")
            return False
        finally:
            conn.close()

    def remove_portfolio_entry(self, isin: str) -> bool:
        """Rimuove un fondo dal portafoglio personale."""
        conn = self._get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM portfolio_entries WHERE isin = %s", (isin,))
                conn.commit()
                return True
        except Exception as e:
            logging.error(f"Errore remove_portfolio_entry {isin}: {e}")
            return False
        finally:
            conn.close()

    def get_portfolio_price_history(self, isin: str, days: int = 60) -> pd.DataFrame:
        """
        Recupera lo storico prezzi per un fondo del portafoglio.
        Ritorna piu' giorni del necessario per permettere il calcolo degli indicatori (SMA20, RSI14).

        Returns:
            DataFrame con colonne [date, price] ordinato per data crescente
        """
        return self.get_prices(isin, days)


def test_database():
    """Test del modulo database"""
    print("="*50)
    print("TEST DATABASE")
    print("="*50)

    db = PriceDatabase()

    today = datetime.now().strftime('%Y-%m-%d')
    print(f"\nSalvataggio prezzo test...")
    success = db.save_price('TEST123', today, 100.50, 'Test')
    print(f"  Risultato: {'OK' if success else 'ERRORE'}")

    print(f"\nRecupero prezzi...")
    prices = db.get_prices('TEST123', 10)
    print(f"  Record trovati: {len(prices)}")

    print(f"\nStatistiche database:")
    stats = db.get_stats()
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    test_database()

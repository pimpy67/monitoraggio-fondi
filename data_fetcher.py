"""
data_fetcher.py - Modulo per recuperare i dati NAV dei fondi
============================================================
Utilizza diverse fonti per ottenere i prezzi dei fondi:
1. Financial Times Markets (principale)
2. Yahoo Finance (backup)
"""

import socket
socket.setdefaulttimeout(25)  # Timeout globale per TUTTE le socket (incluso C code)

import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime, timedelta
import time
import re


class FundDataFetcher:
    """Classe per recuperare dati NAV dei fondi da diverse fonti"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7',
        })
        self.cache = {}
        self.cache_duration = 3600  # 1 ora

    def get_nav_ft_markets(self, isin: str) -> dict:
        """
        Recupera NAV da Financial Times Markets
        Returns: {'price': float, 'date': str, 'currency': str, 'source': str}
        """
        try:
            url = f"https://markets.ft.com/data/funds/tearsheet/summary?s={isin}:EUR"
            response = self.session.get(url, timeout=15)

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')

                # Cerca il prezzo nella pagina
                price_span = soup.find('span', class_='mod-ui-data-list__value')
                if price_span:
                    price_text = price_span.get_text(strip=True)
                    # Pulisci il prezzo (rimuovi simboli, converti virgola in punto)
                    price_clean = re.sub(r'[^\d,\.]', '', price_text).replace(',', '.')
                    if price_clean:
                        try:
                            price = float(price_clean)
                            return {
                                'price': price,
                                'date': datetime.now().strftime('%Y-%m-%d'),
                                'currency': 'EUR',
                                'source': 'FT Markets'
                            }
                        except ValueError:
                            pass
        except Exception as e:
            print(f"Errore FT Markets per {isin}: {e}")

        return None

    def get_nav_yahoo(self, isin: str) -> dict:
        """
        Recupera NAV da Yahoo Finance
        """
        try:
            import yfinance as yf

            # Yahoo Finance accetta ISIN direttamente per alcuni fondi
            fund = yf.Ticker(isin)
            hist = fund.history(period="5d")

            if not hist.empty:
                price = hist['Close'].iloc[-1]
                return {
                    'price': float(price),
                    'date': datetime.now().strftime('%Y-%m-%d'),
                    'currency': 'EUR',
                    'source': 'Yahoo Finance'
                }
        except ImportError:
            print("yfinance non installato. Installa con: pip install yfinance")
        except Exception as e:
            # Yahoo Finance non supporta tutti i fondi, errore silenzioso
            pass

        return None

    def get_nav(self, isin: str) -> dict:
        """
        Recupera NAV usando tutte le fonti disponibili
        Prova in ordine: FT Markets -> Yahoo Finance
        """
        # Controlla cache
        cache_key = f"{isin}_{datetime.now().strftime('%Y%m%d')}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        # Prova le diverse fonti
        result = None

        # 1. Financial Times Markets (più affidabile per fondi europei)
        result = self.get_nav_ft_markets(isin)
        if result:
            self.cache[cache_key] = result
            return result

        time.sleep(0.5)  # Rate limiting

        # 2. Yahoo Finance (backup)
        result = self.get_nav_yahoo(isin)
        if result:
            self.cache[cache_key] = result
            return result

        # Nessun risultato trovato
        return {
            'price': None,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'currency': 'EUR',
            'source': 'N/A',
            'error': 'NAV non disponibile'
        }

    def get_historical_nav_ft(self, isin: str, days: int = 30) -> pd.DataFrame:
        """
        Recupera storico NAV da FT Markets (funziona per tutti i fondi europei)
        URL: https://markets.ft.com/data/funds/tearsheet/historical?s={ISIN}:EUR
        """
        try:
            date_to = datetime.now()
            date_from = date_to - timedelta(days=days + 5)

            url = (
                f"https://markets.ft.com/data/funds/tearsheet/historical"
                f"?s={isin}:EUR"
                f"&from={date_from.strftime('%Y/%m/%d')}"
                f"&to={date_to.strftime('%Y/%m/%d')}"
            )

            response = self.session.get(url, timeout=20, stream=True)
            if response.status_code != 200:
                response.close()
                return pd.DataFrame(columns=['date', 'nav'])

            # Limita la risposta a 2MB — pagine più grandi sono anomale
            content = b''
            for chunk in response.iter_content(chunk_size=65536):
                content += chunk
                if len(content) > 2_000_000:
                    response.close()
                    return pd.DataFrame(columns=['date', 'nav'])
            response.close()

            soup = BeautifulSoup(content.decode('utf-8', errors='replace'), 'html.parser')

            # Cerca la tabella storico prezzi
            table = soup.find('table', class_='mod-ui-table')
            if not table:
                return pd.DataFrame(columns=['date', 'nav'])

            rows = table.find_all('tr')
            records = []

            for row in rows[1:]:  # skip header
                cells = row.find_all('td')
                if len(cells) >= 2:
                    try:
                        date_text = cells[0].get_text(strip=True)
                        # FT usa Close (colonna 5) se disponibile, altrimenti Open (colonna 2)
                        price_text = cells[4].get_text(strip=True) if len(cells) >= 5 else cells[1].get_text(strip=True)

                        # Parsa la data - FT puo' avere formato doppio:
                        # "Wednesday, February 18, 2026Wed, Feb 18, 2026"
                        date_clean = None
                        # Prima prova regex per estrarre il primo formato
                        m = re.match(r'\w+,\s+(\w+)\s+(\d+),\s+(\d{4})', date_text)
                        if m:
                            month_name, day, year = m.groups()
                            try:
                                date_clean = datetime.strptime(f"{month_name} {day}, {year}", "%B %d, %Y").strftime('%Y-%m-%d')
                            except ValueError:
                                pass

                        if not date_clean:
                            for fmt in ['%A, %B %d, %Y', '%d/%m/%Y', '%Y-%m-%d', '%B %d, %Y']:
                                try:
                                    date_clean = datetime.strptime(date_text, fmt).strftime('%Y-%m-%d')
                                    break
                                except ValueError:
                                    continue

                        if not date_clean:
                            continue

                        # Parsa il prezzo
                        price_clean = re.sub(r'[^\d,\.]', '', price_text).replace(',', '.')
                        if price_clean:
                            price = float(price_clean)
                            records.append({'date': date_clean, 'nav': price})
                    except (ValueError, IndexError):
                        continue

            if records:
                df = pd.DataFrame(records)
                df = df.sort_values('date').drop_duplicates(subset='date')
                return df

        except Exception as e:
            print(f"  Errore storico FT Markets per {isin}: {e}")

        return pd.DataFrame(columns=['date', 'nav'])

    def get_historical_nav(self, isin: str, days: int = 30) -> pd.DataFrame:
        """
        Recupera storico NAV per calcolo indicatori tecnici
        Returns: DataFrame con colonne ['date', 'nav'] oppure DataFrame vuoto se non disponibile
        Ordine: FT Markets (tutti i fondi EU) -> Yahoo Finance (backup)
        """
        # 1. FT Markets - funziona per tutti i fondi europei con ISIN
        df = self.get_historical_nav_ft(isin, days)
        if not df.empty and len(df) >= 5:
            print(f"  Storico FT Markets: {len(df)} prezzi per {isin}")
            return df

        time.sleep(0.5)

        # 2. Yahoo Finance (backup, copre pochi fondi EU)
        try:
            import yfinance as yf
            fund = yf.Ticker(isin)
            hist = fund.history(period=f"{days}d")

            if not hist.empty and len(hist) >= 5:
                return pd.DataFrame({
                    'date': hist.index.strftime('%Y-%m-%d').tolist(),
                    'nav': hist['Close'].tolist()
                })
        except:
            pass

        return pd.DataFrame(columns=['date', 'nav'])


    def get_benchmark_history(self, ticker: str, days: int = 120) -> pd.Series:
        """
        Recupera storico prezzi di un benchmark ETF da Yahoo Finance.
        Usato per calcolare Signal Purity Score dei fondi.

        Returns:
            pd.Series con prezzi indicizzati per data (tz-naive), oppure pd.Series vuota
        """
        try:
            import yfinance as yf
            etf = yf.Ticker(ticker)
            hist = etf.history(period=f"{days + 15}d")
            if hist.empty or len(hist) < 20:
                return pd.Series(dtype=float)
            s = hist['Close'].copy()
            # Rimuovi timezone per compatibilità con indici fondi
            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
            return s.tail(days)
        except ImportError:
            print("yfinance non installato")
            return pd.Series(dtype=float)
        except Exception as e:
            print(f"  Benchmark {ticker}: errore fetch ({e})")
            return pd.Series(dtype=float)


def test_fetcher():
    """Test del data fetcher"""
    fetcher = FundDataFetcher()

    # Test con i fondi dell'utente
    test_isins = [
        ("LU1548497772", "Allianz Global AI"),
        ("LU0273159177", "DWS Gold LC"),
        ("LU0408876448", "JPM Global Government Short Duration"),
        ("LU1213836080", "Fidelity Global Technology"),
        ("IT0004782758", "Eurizon Obbligazioni Euro"),
    ]

    print("=" * 60)
    print("TEST DATA FETCHER")
    print("=" * 60)

    successi = 0
    for isin, nome in test_isins:
        print(f"\n{nome} ({isin}):")
        result = fetcher.get_nav(isin)
        if result and result.get('price'):
            print(f"  ✅ NAV: {result['price']} {result['currency']} (fonte: {result['source']})")
            successi += 1
        else:
            print(f"  ❌ NAV non disponibile")
        time.sleep(1)

    print("\n" + "=" * 60)
    print(f"RISULTATO: {successi}/{len(test_isins)} fondi trovati")


if __name__ == "__main__":
    test_fetcher()

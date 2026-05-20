"""
technical_analysis.py - Modulo per analisi tecnica dei fondi
=============================================================
Calcola indicatori tecnici:
- Media Mobile (MM20)
- Pendenza Media Mobile (Slope)
- RSI (Relative Strength Index)
- Bande di Bollinger
- Logica passaggio automatico livelli 3 → 2 → 1
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional


class TechnicalAnalyzer:
    """Classe per calcolare indicatori tecnici sui fondi"""

    # Benchmark ETF per asset_type (ticker Yahoo Finance — London Stock Exchange)
    BENCHMARK_TICKERS = {
        'equity_developed': 'IWDA.L',   # iShares MSCI World
        'emerging_markets': 'EIMI.L',   # iShares MSCI EM IMI
        'sector_thematic':  'IWDA.L',   # proxy: MSCI World
        'commodities':      'CMOD.L',   # iShares Diversified Commodity
        'money_market':     'XEON.L',   # iShares EUR Overnight Rate (ESTER)
        'bond_government':  'XGLE.L',   # Xtrackers Eurozone Govt Bond
        'bond_corporate':   'IEAA.L',   # iShares EUR Corporate Bond
        'high_yield':       'IHYG.L',   # iShares EUR High Yield
    }

    # Classi appartenenti alla famiglia azionaria (usato per Regola E e kill switch)
    EQUITY_FAMILY = frozenset({
        'equity_developed', 'emerging_markets', 'sector_thematic', 'commodities',
        'equity',  # alias retrocompat
    })

    # Profili parametri per le 7 classi operative
    PROFILES = {
        # ── Azionari ─────────────────────────────────────────────────────────
        'equity_developed': {
            'ma_period': 20,
            'ma_period_slow': 50,
            'adx_period': 14,
            'adx_threshold': 25,
            'rsi_period': 14,
            'rsi_oversold': 30,
            'rsi_overbought': 70,
            'bollinger_period': 20,
            'bollinger_std': 2.0,
            'days_above_ma': 5,
            'rsi_optimal_low': 55,
            'rsi_optimal_high': 65,
            'max_distance_from_ma': 2.5,
            'ma_signal_threshold': 2.0,
            'bb_condition': 'upper_half',
        },
        'emerging_markets': {
            # Come equity_developed ma distanza più larga (più volatili)
            'ma_period': 20,
            'ma_period_slow': 50,
            'adx_period': 14,
            'adx_threshold': 25,
            'rsi_period': 14,
            'rsi_oversold': 30,
            'rsi_overbought': 70,
            'bollinger_period': 20,
            'bollinger_std': 2.0,
            'days_above_ma': 5,
            'rsi_optimal_low': 55,
            'rsi_optimal_high': 65,
            'max_distance_from_ma': 3.0,
            'ma_signal_threshold': 2.0,
            'bb_condition': 'upper_half',
        },
        'sector_thematic': {
            # Fondi settoriali/tematici: range RSI leggermente più ampio
            'ma_period': 20,
            'ma_period_slow': 50,
            'adx_period': 14,
            'adx_threshold': 25,
            'rsi_period': 14,
            'rsi_oversold': 30,
            'rsi_overbought': 70,
            'bollinger_period': 20,
            'bollinger_std': 2.0,
            'days_above_ma': 5,
            'rsi_optimal_low': 54,
            'rsi_optimal_high': 66,
            'max_distance_from_ma': 2.5,
            'ma_signal_threshold': 2.0,
            'bb_condition': 'upper_half',
        },
        'commodities': {
            # Materie prime: più volatili, distanza tolleranza maggiore
            'ma_period': 20,
            'ma_period_slow': 50,
            'adx_period': 14,
            'adx_threshold': 25,
            'rsi_period': 14,
            'rsi_oversold': 30,
            'rsi_overbought': 70,
            'bollinger_period': 20,
            'bollinger_std': 2.0,
            'days_above_ma': 5,
            'rsi_optimal_low': 54,
            'rsi_optimal_high': 66,
            'max_distance_from_ma': 3.0,
            'ma_signal_threshold': 2.0,
            'bb_condition': 'upper_half',
        },
        # ── Monetari ─────────────────────────────────────────────────────────
        'money_market': {
            # Fondi monetari e liquidità: NAV cresce quasi linearmente ogni giorno.
            # rsi_exhaustion_threshold alzato a 92: RSI strutturalmente 80-90, soglia 75 causerebbe uscite premature.
            # RSI è strutturalmente alto (80-90) per natura dello strumento, non è
            # "ipercomprato" — non ha senso applicare soglie equity. L'unico segnale
            # rilevante è: NAV sopra MM20 + trend positivo.
            'ma_period': 20,
            'ma_period_slow': 50,
            'rsi_period': 14,
            'rsi_oversold': 25,
            'rsi_overbought': 97,
            'bollinger_period': 20,
            'bollinger_std': 1.0,
            'days_above_ma': 3,
            'rsi_optimal_low': 40,
            'rsi_optimal_high': 95,
            'max_distance_from_ma': 0.5,
            'ma_signal_threshold': 0.1,
            'bb_condition': 'above_ma',
            'rsi_exhaustion_threshold': 92,
        },
        # ── Obbligazionari ───────────────────────────────────────────────────
        'bond_government': {
            # Governativi, breve termine, inflation linked
            'ma_period': 20,
            'ma_period_slow': 50,
            'rsi_period': 14,
            'rsi_oversold': 35,
            'rsi_overbought': 70,
            'bollinger_period': 20,
            'bollinger_std': 1.5,
            'days_above_ma': 3,
            'rsi_optimal_low': 45,
            'rsi_optimal_high': 68,
            'max_distance_from_ma': 1.5,
            'ma_signal_threshold': 0.5,
            'bb_condition': 'above_ma',
        },
        'bond_corporate': {
            # Corporate investment grade: profilo intermedio
            'ma_period': 20,
            'ma_period_slow': 50,
            'rsi_period': 14,
            'rsi_oversold': 35,
            'rsi_overbought': 70,
            'bollinger_period': 20,
            'bollinger_std': 1.7,
            'days_above_ma': 3,
            'rsi_optimal_low': 48,
            'rsi_optimal_high': 65,
            'max_distance_from_ma': 2.0,
            'ma_signal_threshold': 0.8,
            'bb_condition': 'above_ma',
        },
        'high_yield': {
            # HY, EM bond, flessibili, alternativi, bilanciati
            'ma_period': 20,
            'ma_period_slow': 50,
            'rsi_period': 14,
            'rsi_oversold': 33,
            'rsi_overbought': 70,
            'bollinger_period': 20,
            'bollinger_std': 1.8,
            'days_above_ma': 3,
            'rsi_optimal_low': 50,
            'rsi_optimal_high': 66,
            'max_distance_from_ma': 3.0,
            'ma_signal_threshold': 1.0,
            'bb_condition': 'above_ma',
        },
    }
    # Alias retrocompatibilità (vecchi nomi usati da codice esterno)
    PROFILES['equity']  = PROFILES['equity_developed']
    PROFILES['bond']    = PROFILES['bond_government']
    PROFILES['bond_hy'] = PROFILES['high_yield']

    @staticmethod
    def detect_asset_type(categoria: str) -> str:
        """
        Rileva la classe operativa del fondo dalla stringa categoria.

        Classi restituite (8):
            Azionari:
                'equity_developed'  — azionari mercati sviluppati
                'emerging_markets'  — azionari mercati emergenti
                'sector_thematic'   — settoriali/tematici (tech, healthcare, real estate…)
                'commodities'       — materie prime, oro, energia reale
            Monetari:
                'money_market'      — fondi monetari e liquidità (NAV quasi-lineare)
            Obbligazionari:
                'bond_government'   — governativi, breve termine, inflation linked
                'bond_corporate'    — corporate investment grade
                'high_yield'        — HY, EM bond, flessibili, alternativi, bilanciati
        """
        if not categoria:
            return 'equity_developed'
        cat = categoria.lower()

        # ── Monetari → money_market (NAV lineare, RSI strutturalmente alto) ──
        if any(kw in cat for kw in ('monetar', 'money market', 'liquidity')):
            return 'money_market'

        # ── Multi-asset/bilanciati/alternativi → high_yield ─────────────────
        # Questo check è prima di is_bond: "bilanciati" non contiene parole chiave bond
        if any(kw in cat for kw in ('alternativ', 'long-short', 'long short', 'absolute return',
                                     'multi-asset', 'multi asset', 'bilanc', 'altri')):
            return 'high_yield'

        is_bond = any(kw in cat for kw in ('obblig', 'bond', 'fixed', 'reddito'))

        if not is_bond:
            # ── Azionari ────────────────────────────────────────────────────
            if any(kw in cat for kw in ('emerging', 'mercati emergenti', 'em ', 'cina', 'india',
                                         'brasile', 'asia pacific', 'paesi emergenti')):
                return 'emerging_markets'
            if any(kw in cat for kw in ('settoriale', 'thematic', 'tecnolog', 'healthcare',
                                         'salute', 'finanz', 'energia', 'infrastruttur',
                                         'real estate', 'immobil', 'biotech', 'pharma')):
                return 'sector_thematic'
            if any(kw in cat for kw in ('materie prime', 'commodity', 'commodities',
                                         'metalli', 'oro', 'gold', 'petrolio')):
                return 'commodities'
            return 'equity_developed'

        # ── Obbligazionari ───────────────────────────────────────────────────

        if any(kw in cat for kw in ('high yield', 'high-yield')):
            return 'high_yield'

        if any(kw in cat for kw in ('flessibil', 'flexible', 'emerging', 'emergent',
                                     'total return', 'convertib', 'subordinat')):
            return 'high_yield'

        if any(kw in cat for kw in ('corporate', 'credit', 'credito')):
            return 'bond_corporate'

        # Governativi, breve termine, inflation linked, altri obbligazionari standard
        return 'bond_government'

    def __init__(self, config: dict = None, asset_type: str = 'equity_developed'):
        """
        Inizializza l'analizzatore tecnico

        Args:
            config: Dizionario di configurazione (sovrascrive i default del profilo)
            asset_type: Classe operativa del fondo (una delle 7 classi o alias retrocompat)
        """
        self.asset_type = asset_type
        profile = self.PROFILES.get(asset_type, self.PROFILES['equity_developed']).copy()

        # Sovrascritture da config
        if config:
            profile.update(config)

        self.ma_period = profile['ma_period']
        self.ma_period_slow = profile.get('ma_period_slow', 50)
        self.adx_period = profile.get('adx_period', 14)
        self.adx_threshold = profile.get('adx_threshold', 25)
        self.rsi_period = profile['rsi_period']
        self.rsi_oversold = profile['rsi_oversold']
        self.rsi_overbought = profile['rsi_overbought']
        self.bollinger_period = profile['bollinger_period']
        self.bollinger_std = profile['bollinger_std']
        self.days_above_ma = profile['days_above_ma']
        self.rsi_optimal_low = profile['rsi_optimal_low']
        self.rsi_optimal_high = profile['rsi_optimal_high']
        self.max_distance_from_ma = profile['max_distance_from_ma']
        self.ma_signal_threshold = profile.get('ma_signal_threshold', 2.0)
        self.bb_condition = profile.get('bb_condition', 'upper_half')
        # Soglia RSI stanchezza (Regola C uscita L1): per money_market è alta perché RSI è strutturalmente 80-90
        self.rsi_exhaustion_threshold = profile.get('rsi_exhaustion_threshold', 75)
    
    def calculate_ma(self, prices: pd.Series, period: int = None) -> pd.Series:
        """
        Calcola la Media Mobile Semplice (SMA)

        Args:
            prices: Serie di prezzi
            period: Periodo della media (default: self.ma_period)

        Returns:
            Serie con i valori della media mobile
        """
        period = period or self.ma_period
        return prices.rolling(window=period).mean()

    def calculate_ma_slope(self, ma: pd.Series, days: int = 3) -> float:
        """
        Calcola la pendenza della Media Mobile

        Args:
            ma: Serie della media mobile
            days: Numero di giorni per calcolare la pendenza

        Returns:
            Pendenza (positiva = trend rialzista, negativa = ribassista)
        """
        if len(ma) < days + 1:
            return 0.0
        recent_ma = ma.dropna().tail(days + 1)
        if len(recent_ma) < 2:
            return 0.0
        # Pendenza = (MA oggi - MA N giorni fa) / MA N giorni fa * 100
        slope = (recent_ma.iloc[-1] - recent_ma.iloc[0]) / recent_ma.iloc[0] * 100
        return slope

    def calculate_bollinger_bands(self, prices: pd.Series, period: int = None,
                                   std_dev: float = None) -> Dict[str, pd.Series]:
        """
        Calcola le Bande di Bollinger

        Args:
            prices: Serie di prezzi
            period: Periodo per la media (default: self.bollinger_period)
            std_dev: Numero di deviazioni standard (default: self.bollinger_std)

        Returns:
            Dizionario con 'middle' (SMA), 'upper', 'lower', 'width'
        """
        period = period or self.bollinger_period
        std_dev = std_dev or self.bollinger_std

        middle = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()

        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        # Width = ampiezza delle bande (per rilevare squeeze/espansione)
        width = (upper - lower) / middle * 100

        return {
            'middle': middle,
            'upper': upper,
            'lower': lower,
            'width': width
        }

    def is_bollinger_expanding(self, bollinger: Dict[str, pd.Series], days: int = 3) -> bool:
        """
        Verifica se le Bande di Bollinger si stanno espandendo

        Args:
            bollinger: Dizionario con dati Bollinger
            days: Giorni da confrontare

        Returns:
            True se le bande si stanno allargando
        """
        width = bollinger['width'].dropna()
        if len(width) < days + 1:
            return False
        recent_width = width.tail(days + 1)
        # Espansione = width attuale > width di N giorni fa
        return recent_width.iloc[-1] > recent_width.iloc[0]

    def calculate_adx(self, prices: pd.Series, period: int = None) -> pd.Series:
        """
        Calcola ADX approssimato usando soli prezzi di chiusura (NAV).
        Stima +DM/-DM dai movimenti daily: sufficiente per misurare forza del trend
        anche senza dati High/Low.
        """
        period = period or self.adx_period
        delta = prices.diff()
        plus_dm  = delta.clip(lower=0)
        minus_dm = (-delta).clip(lower=0)
        tr = delta.abs()
        alpha = 1.0 / period
        atr      = tr.ewm(alpha=alpha, adjust=False).mean()
        safe_atr = atr.replace(0, np.nan)
        plus_di  = 100 * (plus_dm.ewm(alpha=alpha, adjust=False).mean() / safe_atr)
        minus_di = 100 * (minus_dm.ewm(alpha=alpha, adjust=False).mean() / safe_atr)
        di_sum   = (plus_di + minus_di).replace(0, np.nan)
        dx       = 100 * (plus_di - minus_di).abs() / di_sum
        adx      = dx.ewm(alpha=alpha, adjust=False).mean()
        return adx

    def count_days_above_ma(self, prices: pd.Series, ma: pd.Series, max_days: int = 10) -> int:
        """
        Conta i giorni consecutivi in cui il prezzo è sopra la MM

        Args:
            prices: Serie di prezzi
            ma: Serie della media mobile
            max_days: Massimo giorni da controllare

        Returns:
            Numero di giorni consecutivi sopra la MM
        """
        if len(prices) < 2 or len(ma) < 2:
            return 0

        count = 0
        for i in range(1, min(max_days + 1, len(prices))):
            idx = -i
            if len(prices) >= abs(idx) and len(ma) >= abs(idx):
                price = prices.iloc[idx]
                ma_val = ma.iloc[idx]
                if pd.notna(ma_val) and price > ma_val:
                    count += 1
                else:
                    break
            else:
                break
        return count

    def calculate_ma200(self, prices: pd.Series) -> Optional[float]:
        """
        Calcola MM200. Ritorna None se lo storico è insufficiente (< 200 giorni).
        """
        if len(prices) < 200:
            return None
        return float(prices.rolling(window=200).mean().iloc[-1])

    def detect_positive_divergence(self, prices: pd.Series, rsi: pd.Series,
                                    window: int = 30, recent: int = 10) -> bool:
        """
        Rileva divergenza rialzista: prezzo fa minimo più basso, RSI fa minimo più alto.

        Algoritmo:
        - Cerca il minimo più recente di prezzo negli ultimi `recent` giorni
        - Cerca il minimo precedente nella finestra [window-recent] giorni prima
        - Divergenza = prezzo al minimo recente < prezzo al minimo precedente
                       E RSI al minimo recente > RSI al minimo precedente
        """
        rsi_clean = rsi.dropna()
        if len(prices) < window or len(rsi_clean) < window:
            return False

        prices_w = prices.tail(window)
        rsi_w    = rsi_clean.tail(window)

        # Minimo recente (ultimi `recent` giorni)
        recent_prices = prices_w.tail(recent)
        recent_rsi    = rsi_w.tail(recent)
        if len(recent_prices) < 3 or len(recent_rsi) < 3:
            return False
        r_min_pos   = int(recent_prices.values.argmin())
        r_price_min = float(recent_prices.iloc[r_min_pos])
        r_rsi_min   = float(recent_rsi.iloc[r_min_pos])

        # Minimo precedente (giorni precedenti ai `recent`)
        older_prices = prices_w.iloc[:-recent]
        older_rsi    = rsi_w.iloc[:-recent]
        if len(older_prices) < 5 or len(older_rsi) < 5:
            return False
        o_min_pos   = int(older_prices.values.argmin())
        o_price_min = float(older_prices.iloc[o_min_pos])
        o_rsi_min   = float(older_rsi.iloc[o_min_pos])

        price_lower_low  = r_price_min < o_price_min
        rsi_higher_low   = r_rsi_min   > o_rsi_min

        return price_lower_low and rsi_higher_low

    def detect_rsi_recovery(self, rsi: pd.Series,
                             oversold: float = 30.0,
                             recovery: float = 32.0,
                             lookback: int = 10) -> bool:
        """
        RSI era in ipervenduto (< oversold) negli ultimi `lookback` giorni
        e ora è risalito sopra `recovery`.
        """
        rsi_clean = rsi.dropna()
        if len(rsi_clean) < 3:
            return False
        recent = rsi_clean.tail(lookback)
        was_oversold   = any(v < oversold  for v in recent.iloc[:-1])
        now_recovering = float(recent.iloc[-1]) > recovery
        return was_oversold and now_recovering

    def detect_micro_breakout(self, prices: pd.Series, lookback: int = 5,
                               min_pct: float = 0.3) -> bool:
        """
        Il prezzo di oggi supera il massimo degli ultimi `lookback` giorni
        di almeno `min_pct`%. Conferma che la pressione ribassista è ceduta.
        """
        if len(prices) < lookback + 2:
            return False
        recent_high = float(prices.iloc[-(lookback + 1):-1].max())
        current     = float(prices.iloc[-1])
        if recent_high == 0:
            return False
        return current > recent_high * (1 + min_pct / 100)

    def suggest_level_0(self, prices: pd.Series, current_level: int) -> Dict:
        """
        Valuta le condizioni L0 "Deep Recovery".

        Condizioni di ENTRATA (tutte e 4 obbligatorie):
          1. NAV almeno 15% sotto MM200
          2. RSI(14) < 30 (ipervenduto)
          3. Divergenza rialzista (prezzo minimo più basso, RSI minimo più alto)
          4. Segnale di recupero: RSI risalito > 32 OPPURE micro-breakout prezzo

        Condizioni di USCITA (già in L0 — basta 1):
          α. NAV < panic_low (stop loss assoluto)
          β. RSI < 25 dopo l'ingresso (trappola ribassista)
          γ. NAV > MM20 (take profit parziale → exit L0, promozione naturale verso L2)
          ε. Nessun recupero dopo 30gg (gestito in monitor.py con entry_date)

        Returns:
            Dict con 'l0_entry' (bool), 'l0_exit_rule', condizioni e valori.
        """
        result = {
            'l0_entry': False,
            'l0_exit_rule': None,
            'l0_exit_trigger': None,
            'peak_price': None,
            'peak_days': None,
            'distance_from_peak': None,
            'rsi_oversold': False,
            'divergence': False,
            'rsi_recovery': False,
            'micro_breakout': False,
            'reason_codes': [],
        }

        if len(prices) < 20:
            result['reason_codes'] = ['INSUFFICIENT_DATA']
            return result

        rsi = self.calculate_rsi(prices)
        rsi_current = float(rsi.dropna().iloc[-1]) if len(rsi.dropna()) > 0 else 50.0
        current_price = float(prices.iloc[-1])

        ma = self.calculate_ma(prices)
        ma_current = float(ma.iloc[-1]) if pd.notna(ma.iloc[-1]) else None

        # Esponi RSI e MM20 correnti nel risultato (utili per alert e digest)
        result['rsi'] = round(rsi_current, 1)
        result['mm20_current'] = round(ma_current, 4) if ma_current else None
        result['current_price'] = round(current_price, 4)

        # ── Kill switch: blocca nuovi ingressi su crollo giornaliero ≥ 3% ──
        kill_switch_active = False
        if len(prices) >= 2 and float(prices.iloc[-2]) != 0:
            daily_chg = (float(prices.iloc[-1]) - float(prices.iloc[-2])) / float(prices.iloc[-2]) * 100
            kill_switch_active = daily_chg <= -3.0
            result['daily_change_pct'] = round(daily_chg, 2)
        result['kill_switch'] = kill_switch_active

        # ── Soglia RSI oversold adattata alla classe ─────────────────────────
        l0_rsi_threshold = self.rsi_oversold  # calibrato per classe (30/33/35/38)

        # ── Condizioni di uscita se già in L0 ────────────────────────────────
        if current_level == 0:
            if ma_current and current_price > ma_current:
                result['l0_exit_rule'] = 'gamma'
                result['l0_exit_trigger'] = (
                    f'NAV {current_price:.4f} > MM20 {ma_current:.4f} — take profit, promuovi a L2'
                )
                result['reason_codes'] = ['L0_EXIT_GAMMA']
            elif rsi_current < 25:
                result['l0_exit_rule'] = 'beta'
                result['l0_exit_trigger'] = (
                    f'RSI={rsi_current:.0f} < 25 dopo ingresso — trappola ribassista, esci'
                )
                result['reason_codes'] = ['L0_EXIT_BETA']
            else:
                result['reason_codes'] = ['L0_HOLD']
            # Nota: regola α (NAV < panic_low) e ε (30gg senza recupero)
            # sono gestite in monitor.py con entry_price e entry_date.
            return result

        # ── Condizioni di entrata ────────────────────────────────────────────
        # 1. Contesto di crollo: NAV almeno 15% sotto il picco storico disponibile
        #    Usa il massimo su tutto lo storico disponibile (fino a 200gg).
        #    Non richiede 200 giorni: funziona con i dati che abbiamo (min 20gg).
        peak_price = float(prices.max())
        peak_days = len(prices)
        result['peak_price'] = round(peak_price, 4)
        result['peak_days'] = peak_days

        distance_from_peak = (current_price - peak_price) / peak_price * 100
        result['distance_from_peak'] = round(distance_from_peak, 2)
        cond1_drawdown = distance_from_peak <= -15.0

        # Panic low: minimo degli ultimi 30 giorni (stop loss assoluto al momento dell'ingresso)
        n_panic = min(30, len(prices))
        result['panic_low'] = float(prices.iloc[-n_panic:].min())

        # 2. RSI in ipervenduto (soglia per classe)
        cond2_oversold = rsi_current < l0_rsi_threshold
        result['rsi_oversold'] = cond2_oversold

        # 3. Divergenza rialzista
        cond3_divergence = self.detect_positive_divergence(prices, rsi)
        result['divergence'] = cond3_divergence

        # 4. Segnale di recupero: RSI risalito > 32 OPPURE micro-breakout
        rsi_rec        = self.detect_rsi_recovery(rsi, oversold=l0_rsi_threshold, recovery=32.0)
        micro_break    = self.detect_micro_breakout(prices, lookback=5, min_pct=0.3)
        cond4_recovery = rsi_rec or micro_break
        result['rsi_recovery']   = rsi_rec
        result['micro_breakout'] = micro_break

        entry_ok = cond1_drawdown and cond2_oversold and cond3_divergence and cond4_recovery
        if entry_ok and kill_switch_active:
            # Kill switch: condizioni soddisfatte ma nuovo ingresso bloccato
            result['l0_entry'] = False
            result['reason_codes'] = ['KILL_SWITCH', 'L0_ENTRY_BLOCKED']
        elif entry_ok:
            result['l0_entry'] = True
            result['reason_codes'] = ['L0_ENTRY']
        else:
            result['l0_entry'] = False
            # Dettaglio condizioni mancanti per il log
            missing = []
            if not cond1_drawdown:
                missing.append('L0_COND_DRAWDOWN')
            if not cond2_oversold:
                missing.append('L0_COND_RSI')
            if not cond3_divergence:
                missing.append('L0_COND_DIVERGENCE')
            if not cond4_recovery:
                missing.append('L0_COND_RECOVERY')
            result['reason_codes'] = missing if missing else ['L0_WAIT']

        return result

    def count_rising_days(self, prices: pd.Series, window: int = 5) -> int:
        """
        Conta quanti degli ultimi N giorni il NAV e' salito (non consecutivi).
        Piu' robusto dei giorni consecutivi: basta 1 giorno di pausa
        per non azzerare il conteggio.

        Args:
            prices: Serie di prezzi
            window: Finestra di giorni da controllare

        Returns:
            Numero di giorni in salita su window
        """
        if len(prices) < 2:
            return 0
        count = 0
        for i in range(1, min(window + 1, len(prices))):
            if prices.iloc[-i] > prices.iloc[-i - 1]:
                count += 1
        return count

    def suggest_level(self, prices: pd.Series, current_level: int = 3) -> Dict:
        """
        Suggerisce il livello appropriato per un fondo — Schema L1 "Trend Sicuro"

        Condizioni L1 (6/6 obbligatorie):
        1. ALLINEAMENTO: NAV > MM20 e MM20 > MM50
        2. PERSISTENZA: NAV sopra MM20 per N gg consecutivi + slope MM20 positivo
        3. MOMENTUM RSI: RSI nel range ottimale della classe (es. 55-65 equity)
        4. DISTANZA: NAV sopra MM20 di max % (non tirato)
        5. ADX: ADX(14) > soglia (solo equity; esentato per bond/monetari)
        6. TREND BREVE: NAV oggi > NAV 3 giorni fa (no ingressi in pullback)

        Livelli:
        - Livello 3: Prezzo sotto MM20 (monitoraggio passivo)
        - Livello 2: Prezzo > MM20 per 3+ giorni consecutivi
        - Livello 1: Tutte e 6 le condizioni soddisfatte

        Args:
            prices: Serie storica dei prezzi
            current_level: Livello attuale del fondo

        Returns:
            Dizionario con livello suggerito e motivazione
        """
        if len(prices) < self.ma_period:
            return {
                'suggested_level': current_level,
                'reason': 'Dati insufficienti per analisi',
                'reason_codes': ['INSUFFICIENT_DATA'],
                'conditions': {}
            }

        # ── Kill switch: blocca nuovi ingressi su crollo giornaliero ≥ 3% ──
        kill_switch_active = False
        daily_change_pct = None
        if len(prices) >= 2 and float(prices.iloc[-2]) != 0:
            daily_change_pct = (float(prices.iloc[-1]) - float(prices.iloc[-2])) / float(prices.iloc[-2]) * 100
            kill_switch_active = daily_change_pct <= -3.0

        # Calcola indicatori
        ma = self.calculate_ma(prices)               # MM20
        ma_current = ma.iloc[-1]
        current_price = prices.iloc[-1]

        # MM50 (se disponibile)
        if len(prices) >= self.ma_period_slow:
            ma50 = self.calculate_ma(prices, self.ma_period_slow)
            ma50_current = ma50.iloc[-1]
        else:
            ma50_current = None

        rsi = self.calculate_rsi(prices)
        rsi_current = rsi.iloc[-1] if len(rsi) > 0 and pd.notna(rsi.iloc[-1]) else 50

        ma_slope = self.calculate_ma_slope(ma)
        days_above = self.count_days_above_ma(prices, ma)

        bollinger = self.calculate_bollinger_bands(prices)
        bb_upper = bollinger['upper'].iloc[-1] if pd.notna(bollinger['upper'].iloc[-1]) else None

        # ADX
        adx_series  = self.calculate_adx(prices)
        adx_current = float(adx_series.iloc[-1]) if len(adx_series) > 0 and pd.notna(adx_series.iloc[-1]) else 0.0

        # MM5 (per Regola B di uscita)
        ma5 = self.calculate_ma(prices, 5)
        ma5_current = float(ma5.iloc[-1]) if pd.notna(ma5.iloc[-1]) else None

        # RSI precedente (per Regola C di uscita)
        rsi_clean = rsi.dropna()
        rsi_prev = float(rsi_clean.iloc[-2]) if len(rsi_clean) >= 2 else rsi_current

        # Distanza % da MM20
        distance_from_ma = ((current_price - ma_current) / ma_current * 100) if pd.notna(ma_current) and ma_current != 0 else 0

        # ROC NAV: variazione % del prezzo grezzo su 3 e 5 giorni
        # Serve a rilevare se il NAV è già in discesa anche quando la MM20 è ancora inerzialmente positiva
        if len(prices) >= 4:
            price_3d_ago = float(prices.iloc[-4])
            roc_3d = (float(current_price) - price_3d_ago) / price_3d_ago * 100 if price_3d_ago != 0 else 0.0
            roc_3d_ok = roc_3d > 0
        else:
            roc_3d = 0.0
            roc_3d_ok = False

        if len(prices) >= 6:
            price_5d_ago = float(prices.iloc[-6])
            roc_5d = (float(current_price) - price_5d_ago) / price_5d_ago * 100 if price_5d_ago != 0 else 0.0
            roc_5d_ok = roc_5d > 0
        else:
            roc_5d = 0.0
            roc_5d_ok = False

        # Retro-compat (usati nel conditions dict per il dashboard)
        nav_rising_alt = roc_5d > 0
        pct_vs_5d = round(roc_5d, 2)
        nav_momentum_ok = roc_3d > 0
        pct_vs_3d = round(roc_3d, 2)

        # ── CONDIZIONI L1 "TREND SICURO" (6 condizioni, tutte obbligatorie) ──────
        price_above_ma  = current_price > ma_current if pd.notna(ma_current) else False
        slope_positive  = ma_slope > 0

        # 1. ALLINEAMENTO: Price > MM20 AND MM20 > MM50
        mm20_above_mm50 = (pd.notna(ma_current) and ma50_current is not None
                           and pd.notna(ma50_current) and float(ma_current) > float(ma50_current))
        allineamento_ok = price_above_ma and mm20_above_mm50

        # 2. PERSISTENZA: ≥5 giorni consecutivi sopra MM20 + slope↑
        price_above_ma_ndays = days_above >= self.days_above_ma
        persistenza_ok = price_above_ma_ndays and slope_positive

        # 3. MOMENTUM: RSI 55–65
        rsi_optimal = self.rsi_optimal_low <= rsi_current <= self.rsi_optimal_high

        # 4. DISTANZA: NAV sopra MM20 di max 2.5%
        distance_ok = 0 <= distance_from_ma < self.max_distance_from_ma

        # 5. ADX: > 25 (trend con direzione)
        adx_ok = adx_current > self.adx_threshold
        # Per obbligazionari ADX fisiologicamente basso è normale: la condizione è esentata
        adx_entry_ok = adx_ok if self.asset_type in self.EQUITY_FAMILY else True

        # Retro-compat (usato da exit logic e buy_count)
        trend_ok = persistenza_ok
        nav_rising = nav_rising_alt
        price_above_ma_3days = days_above >= 3  # per L2

        # Bollinger (usato solo per segnali generali, non per L1 entry)
        if self.bb_condition == 'upper_half' and bb_upper is not None and ma_current is not None:
            bb_midpoint = (ma_current + bb_upper) / 2
            nav_above_upper_bb = current_price >= bb_midpoint
        elif self.bb_condition == 'above_ma' and ma_current is not None:
            nav_above_upper_bb = current_price > ma_current
        else:
            nav_above_upper_bb = False

        rising_days = self.count_rising_days(prices, window=5)

        # 6. PENDENZA NAV: il NAV grezzo deve essere in salita su entrambi i timeframe
        #    e almeno 3 giorni su 5 devono essere chiusure positive.
        #    Blocca ingressi quando il NAV è già in pullback mentre la MM20 è ancora inerzialmente alta.
        nav_consistency_ok = rising_days >= 3
        nav_trend_ok = roc_3d_ok and roc_5d_ok
        nav_pendenza_ok = nav_trend_ok and nav_consistency_ok

        conditions = {
            'price_above_ma': price_above_ma,
            'days_above_ma': days_above,
            'price_above_ma_3days': price_above_ma_3days,
            'ma_slope': round(ma_slope, 3),
            'slope_positive': slope_positive,
            'distance_from_ma': round(distance_from_ma, 2),
            'distance_ok': distance_ok,
            'rsi': round(rsi_current, 1),
            'rsi_optimal': rsi_optimal,
            'bb_upper': round(bb_upper, 4) if bb_upper else None,
            'nav_above_upper_bb': nav_above_upper_bb,
            'rising_days': rising_days,
            'nav_rising_alt': nav_rising_alt,
            'nav_rising': nav_rising,
            'pct_vs_5d': round(pct_vs_5d, 2),
            # Nuove condizioni L1 Trend Sicuro
            'mm20_current': round(float(ma_current), 4) if pd.notna(ma_current) else None,
            'mm50_current': round(float(ma50_current), 4) if ma50_current is not None and pd.notna(ma50_current) else None,
            'mm20_above_mm50': mm20_above_mm50,
            'allineamento_ok': allineamento_ok,
            'persistenza_ok': persistenza_ok,
            'adx': round(adx_current, 1),
            'adx_ok': adx_ok,
            'adx_entry_ok': adx_entry_ok,
            # Per regole uscita
            'mm5_current': round(ma5_current, 4) if ma5_current is not None else None,
            'rsi_prev': round(rsi_prev, 1),
            # Aggregati (per buy_count e retro-compat)
            'trend_ok': trend_ok,
            # 6a condizione L1 — pendenza NAV grezzo
            'nav_momentum_ok': nav_momentum_ok,   # retro-compat (= roc_3d > 0)
            'pct_vs_3d': pct_vs_3d,
            'roc_3d': round(roc_3d, 3),
            'roc_5d': round(roc_5d, 3),
            'roc_3d_ok': roc_3d_ok,
            'roc_5d_ok': roc_5d_ok,
            'nav_trend_ok': nav_trend_ok,
            'nav_consistency_ok': nav_consistency_ok,
            'nav_pendenza_ok': nav_pendenza_ok,
        }

        # Segnali uscita L1 (calcolati sempre, usati solo se current_level==1)
        ma5_below_ma20  = (ma5_current is not None and pd.notna(ma_current)
                           and ma5_current < float(ma_current))
        rsi_exhaustion  = rsi_prev > self.rsi_exhaustion_threshold and rsi_current < rsi_prev

        # Determina livello suggerito
        reason_codes = []

        if kill_switch_active:
            conditions['kill_switch'] = True
            conditions['daily_change_pct'] = round(daily_change_pct, 2) if daily_change_pct is not None else None
        else:
            conditions['kill_switch'] = False
            conditions['daily_change_pct'] = round(daily_change_pct, 2) if daily_change_pct is not None else None

        if current_level == 1:
            # USCITA L1 — 6 REGOLE (in ordine di priorità):
            # Regola A: NAV < MM20 → stop loss, tesi fallita
            # Regola B: MM5 < MM20 → trailing stop, rally esaurito
            # Regola F: MM50 non calcolabile → storico insufficiente
            # Regola D: MM20 < MM50 → strutturale, trend deteriorato
            # Regola E: ADX < 25 (solo classi azionarie) → trend perde forza
            # Regola C: RSI > 75 e piega in giù → stanchezza, vendi in cima
            if not price_above_ma:
                conditions['exit_rule'] = 1
                conditions['exit_trigger'] = f'NAV {float(current_price):.4f} < MM20 {float(ma_current):.4f}'
                suggested = 3
                reason = f'Uscita L1 [Regola A — Stop Loss]: NAV sceso sotto MM20'
                reason_codes.append('L1_EXIT_STOP_LOSS')
            elif ma5_below_ma20:
                conditions['exit_rule'] = 2
                conditions['exit_trigger'] = f'MM5={ma5_current:.4f} < MM20={float(ma_current):.4f}'
                suggested = 3
                reason = f'Uscita L1 [Regola B — Trailing Stop]: MM5 ha incrociato MM20 al ribasso'
                reason_codes.append('L1_EXIT_TRAILING')
            elif ma50_current is None:
                conditions['exit_rule'] = 6
                conditions['exit_trigger'] = f'MM50 non calcolabile (storico < {self.ma_period_slow} giorni)'
                suggested = 3
                reason = f'Uscita L1 [Regola F — Storico]: MM50 non calcolabile — fondo troppo giovane per L1'
                reason_codes.append('L1_EXIT_HISTORY')
            elif not mm20_above_mm50 and ma50_current is not None:
                conditions['exit_rule'] = 4
                conditions['exit_trigger'] = f'MM20={float(ma_current):.4f} < MM50={float(ma50_current):.4f}'
                suggested = 3
                reason = f'Uscita L1 [Regola D — Strutturale MM]: MM20 sceso sotto MM50 — trend a medio termine deteriorato'
                reason_codes.append('L1_EXIT_STRUCTURAL')
            elif not adx_ok and self.asset_type in self.EQUITY_FAMILY:
                # Regola E solo per classi azionarie: per bond/HY/alternativi ADX basso è fisiologico
                conditions['exit_rule'] = 5
                conditions['exit_trigger'] = f'ADX={adx_current:.0f} < {self.adx_threshold} (soglia)'
                suggested = 3
                reason = f'Uscita L1 [Regola E — ADX debole]: ADX={adx_current:.0f} sotto soglia {self.adx_threshold} — trend perde forza'
                reason_codes.append('L1_EXIT_ADX_WEAK')
            elif rsi_exhaustion:
                conditions['exit_rule'] = 3
                conditions['exit_trigger'] = f'RSI era {rsi_prev:.0f} > {self.rsi_exhaustion_threshold}, ora {rsi_current:.0f} (↓)'
                suggested = 3
                reason = f'Uscita L1 [Regola C — Stanchezza]: RSI={rsi_current:.0f} in discesa da {rsi_prev:.0f}'
                reason_codes.append('L1_EXIT_EXHAUSTION')
            else:
                conditions['exit_rule'] = None
                conditions['exit_trigger'] = None
                suggested = 1
                reason = f'Mantenuto L1 — NAV sopra MM5 e MM20 (RSI {rsi_current:.0f}, ADX {adx_current:.0f})'
                reason_codes.append('L1_HOLD')
        elif not price_above_ma:
            suggested = 3
            reason = 'Prezzo sotto MM20'
            reason_codes.append('L3_MONITOR')
        elif ma50_current is None:
            suggested = 2 if price_above_ma_3days else 3
            reason = f'Storico insufficiente per MM50 (min 50 giorni) — ingresso L1 bloccato'
            reason_codes.append('L2_WATCHLIST' if price_above_ma_3days else 'L3_MONITOR')
        elif allineamento_ok and persistenza_ok and rsi_optimal and distance_ok and adx_entry_ok and nav_pendenza_ok:
            if kill_switch_active:
                # Kill switch: tutte le condizioni L1 ok ma blocchiamo il nuovo ingresso
                suggested = current_level
                reason = (f'Kill Switch attivo [{daily_change_pct:.1f}%]: nuovo ingresso L1 bloccato — '
                          f'revisione manuale richiesta')
                reason_codes.extend(['KILL_SWITCH', 'L1_ENTRY_BLOCKED'])
            else:
                suggested = 1
                adx_note = (f'ADX {adx_current:.0f} ✓' if self.asset_type in self.EQUITY_FAMILY
                            else 'ADX n/a (obblig.)')
                reason = (f'L1 Trend Sicuro: MM20>MM50 ✓, {days_above}gg sopra MM20 ✓, '
                          f'RSI {rsi_current:.0f} ✓, dist {distance_from_ma:.1f}% ✓, {adx_note}, '
                          f'trend 3gg +{pct_vs_3d:.2f}% ✓')
                reason_codes.append('L1_ENTRY')
        elif price_above_ma_3days:
            suggested = 2
            reason = f'Prezzo sopra MM20 da {days_above} giorni consecutivi'
            reason_codes.append('L2_WATCHLIST')
        elif price_above_ma:
            suggested = 3
            reason = f'Prezzo sopra MM20 da {days_above} giorni (servono {self.days_above_ma})'
            reason_codes.append('L3_MONITOR')
        else:
            suggested = 3
            reason = 'Monitoraggio passivo'
            reason_codes.append('L3_MONITOR')

        return {
            'suggested_level': suggested,
            'current_level': current_level,
            'level_change': suggested != current_level,
            'reason': reason,
            'reason_codes': reason_codes,
            'conditions': conditions
        }
    
    def calculate_rsi(self, prices: pd.Series, period: int = None) -> pd.Series:
        """
        Calcola il Relative Strength Index (RSI)
        
        Formula:
        RSI = 100 - (100 / (1 + RS))
        RS = Media guadagni / Media perdite
        
        Args:
            prices: Serie di prezzi
            period: Periodo RSI (default: self.rsi_period)
        
        Returns:
            Serie con i valori RSI
        """
        period = period or self.rsi_period
        
        # Calcola variazioni giornaliere
        delta = prices.diff()
        
        # Separa guadagni e perdite
        gains = delta.where(delta > 0, 0)
        losses = (-delta).where(delta < 0, 0)
        
        # Media mobile esponenziale dei guadagni e perdite
        avg_gain = gains.ewm(com=period-1, min_periods=period).mean()
        avg_loss = losses.ewm(com=period-1, min_periods=period).mean()
        
        # Calcola RS e RSI
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def calculate_macd(self, prices: pd.Series, 
                       fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, pd.Series]:
        """
        Calcola il MACD (Moving Average Convergence Divergence)
        
        Args:
            prices: Serie di prezzi
            fast: Periodo EMA veloce (default: 12)
            slow: Periodo EMA lento (default: 26)
            signal: Periodo linea segnale (default: 9)
        
        Returns:
            Dizionario con 'macd', 'signal', 'histogram'
        """
        ema_fast = prices.ewm(span=fast).mean()
        ema_slow = prices.ewm(span=slow).mean()
        
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal).mean()
        histogram = macd_line - signal_line
        
        return {
            'macd': macd_line,
            'signal': signal_line,
            'histogram': histogram
        }
    
    def get_price_vs_ma_signal(self, current_price: float, ma_value: float) -> str:
        """
        Determina il segnale basato su prezzo vs media mobile
        
        Returns:
            'BUY' se prezzo > MA (trend rialzista)
            'SELL' se prezzo < MA (trend ribassista)
            'HOLD' se dati insufficienti
        """
        if ma_value is None or np.isnan(ma_value):
            return 'HOLD'
        
        pct_diff = (current_price - ma_value) / ma_value * 100
        threshold = self.ma_signal_threshold

        if pct_diff > threshold:
            return 'BUY'
        elif pct_diff < -threshold:
            return 'SELL'
        else:
            return 'HOLD'
    
    def get_rsi_signal(self, rsi_value: float) -> str:
        """
        Determina il segnale basato su RSI
        
        Returns:
            'BUY' se RSI < soglia ipervenduto
            'SELL' se RSI > soglia ipercomprato
            'HOLD' altrimenti
        """
        if rsi_value is None or np.isnan(rsi_value):
            return 'HOLD'
        
        if rsi_value < self.rsi_oversold:
            return 'BUY'
        elif rsi_value > self.rsi_overbought:
            return 'SELL'
        else:
            return 'HOLD'
    
    def get_macd_signal(self, macd: float, signal: float, prev_macd: float = None, prev_signal: float = None) -> str:
        """
        Determina il segnale basato su MACD
        
        Returns:
            'BUY' se MACD incrocia al rialzo la signal line
            'SELL' se MACD incrocia al ribasso la signal line
            'HOLD' altrimenti
        """
        if macd is None or signal is None:
            return 'HOLD'
        
        # Se abbiamo dati precedenti, controlliamo l'incrocio
        if prev_macd is not None and prev_signal is not None:
            if prev_macd < prev_signal and macd > signal:
                return 'BUY'  # Incrocio rialzista
            elif prev_macd > prev_signal and macd < signal:
                return 'SELL'  # Incrocio ribassista
        
        # Altrimenti usiamo la posizione relativa
        if macd > signal:
            return 'BUY'
        elif macd < signal:
            return 'SELL'
        
        return 'HOLD'
    
    def get_combined_signal(self, signals: List[str], min_agreement: int = 2) -> Tuple[str, int]:
        """
        Combina più segnali per ottenere un segnale finale
        
        Args:
            signals: Lista di segnali ('BUY', 'SELL', 'HOLD')
            min_agreement: Numero minimo di segnali concordi richiesti
        
        Returns:
            Tupla (segnale_finale, numero_indicatori_concordi)
        """
        buy_count = signals.count('BUY')
        sell_count = signals.count('SELL')
        
        if buy_count >= min_agreement:
            return ('BUY', buy_count)
        elif sell_count >= min_agreement:
            return ('SELL', sell_count)
        else:
            return ('HOLD', 0)
    
    def analyze_fund(self, prices: pd.Series, level: int = 3) -> Dict:
        """
        Esegue analisi tecnica completa su un fondo

        Args:
            prices: Serie storica dei prezzi (più recente all'ultimo indice)
            level: Livello attuale del fondo (1, 2, 3)

        Returns:
            Dizionario con tutti gli indicatori, segnali e livello suggerito
        """
        if len(prices) < self.ma_period:
            days_available = len(prices)
            days_needed = self.ma_period
            # Calcola le percentuali possibili anche con dati insufficienti per MM
            pct_1d = round(float((prices.iloc[-1] - prices.iloc[-2]) / prices.iloc[-2] * 100), 2) if len(prices) >= 2 else None
            pct_1w = round(float((prices.iloc[-1] - prices.iloc[-6]) / prices.iloc[-6] * 100), 2) if len(prices) >= 6 else None
            pct_1m = round(float((prices.iloc[-1] - prices.iloc[-22]) / prices.iloc[-22] * 100), 2) if len(prices) >= 22 else None
            return {
                'current_price': float(prices.iloc[-1]) if len(prices) > 0 else None,
                'ma': None,
                'ma_slope': None,
                'rsi': None,
                'bollinger': None,
                'days_above_ma': 0,
                'final_signal': 'HOLD',
                'signal_strength': 0,
                'suggested_level': level,
                'level_change': False,
                'level_reason': f'Dati insufficienti: {days_available}/{days_needed} giorni',
                'buy_count': 0,
                'pct_change_1d': pct_1d,
                'pct_change_1w': pct_1w,
                'pct_change_1m': pct_1m,
                'data_status': 'insufficient',
                'error': f'Dati insufficienti: {days_available}/{days_needed} giorni. Attendere accumulo storico.'
            }

        current_price = prices.iloc[-1]

        # Variazioni percentuali (float() per evitare numpy.float64)
        pct_1d = round(float((prices.iloc[-1] - prices.iloc[-2]) / prices.iloc[-2] * 100), 2) if len(prices) >= 2 else None
        pct_1w = round(float((prices.iloc[-1] - prices.iloc[-6]) / prices.iloc[-6] * 100), 2) if len(prices) >= 6 else None
        pct_1m = round(float((prices.iloc[-1] - prices.iloc[-22]) / prices.iloc[-22] * 100), 2) if len(prices) >= 22 else None

        # Calcola indicatori
        ma = self.calculate_ma(prices)
        ma_current = ma.iloc[-1]
        ma_slope = self.calculate_ma_slope(ma)

        rsi = self.calculate_rsi(prices)
        rsi_current = rsi.iloc[-1]

        bollinger = self.calculate_bollinger_bands(prices)
        bb_width = bollinger['width'].iloc[-1] if pd.notna(bollinger['width'].iloc[-1]) else 0
        bb_expanding = self.is_bollinger_expanding(bollinger)

        days_above = self.count_days_above_ma(prices, ma)

        # Segnali per compatibilità
        ma_signal = self.get_price_vs_ma_signal(current_price, ma_current)
        rsi_signal = self.get_rsi_signal(rsi_current)
        signals = [ma_signal, rsi_signal]

        # MACD per livelli 1 e 2
        macd_data = None
        macd_signal = 'HOLD'

        if level <= 2 and len(prices) >= 26:
            macd_data = self.calculate_macd(prices)
            macd_current = macd_data['macd'].iloc[-1]
            signal_current = macd_data['signal'].iloc[-1]

            prev_macd = macd_data['macd'].iloc[-2] if len(macd_data['macd']) > 1 else None
            prev_signal = macd_data['signal'].iloc[-2] if len(macd_data['signal']) > 1 else None

            macd_signal = self.get_macd_signal(macd_current, signal_current, prev_macd, prev_signal)
            signals.append(macd_signal)

        # Segnale combinato
        final_signal, strength = self.get_combined_signal(signals, min_agreement=2)

        # Suggerimento livello automatico
        level_suggestion = self.suggest_level(prices, current_level=level)

        # Conteggio condizioni BUY L1 Trend Sicuro (6 condizioni)
        lc = level_suggestion['conditions']
        buy_count = sum([
            lc.get('allineamento_ok', False),    # 1. MM20>MM50 + price>MM20
            lc.get('persistenza_ok', False),     # 2. 5gg sopra MM20 + slope+
            lc.get('rsi_optimal', False),        # 3. RSI nel range target
            lc.get('distance_ok', False),        # 4. dist max da MM20
            lc.get('adx_entry_ok', lc.get('adx_ok', False)),  # 5. ADX>25 (equity) / esentato (bond)
            lc.get('nav_pendenza_ok', False),    # 6. ROC_3>0 + ROC_5>0 + rising_days≥3
        ])

        # Calcola distanza dal max 52 settimane
        max_52w = prices.tail(252).max() if len(prices) >= 252 else prices.max()
        pct_from_high = (current_price - max_52w) / max_52w * 100

        return {
            'current_price': round(current_price, 4),
            'ma': round(ma_current, 4) if pd.notna(ma_current) else None,
            'ma_slope': round(ma_slope, 3),
            'ma_signal': ma_signal,
            'rsi': round(rsi_current, 2) if pd.notna(rsi_current) else None,
            'rsi_signal': rsi_signal,
            'bollinger': {
                'upper': round(bollinger['upper'].iloc[-1], 4) if pd.notna(bollinger['upper'].iloc[-1]) else None,
                'middle': round(bollinger['middle'].iloc[-1], 4) if pd.notna(bollinger['middle'].iloc[-1]) else None,
                'lower': round(bollinger['lower'].iloc[-1], 4) if pd.notna(bollinger['lower'].iloc[-1]) else None,
                'width': round(bb_width, 2),
                'expanding': bb_expanding
            },
            'days_above_ma': days_above,
            'macd': {
                'line': round(macd_data['macd'].iloc[-1], 4) if macd_data else None,
                'signal': round(macd_data['signal'].iloc[-1], 4) if macd_data else None,
                'histogram': round(macd_data['histogram'].iloc[-1], 4) if macd_data else None
            },
            'macd_signal': macd_signal,
            'final_signal': final_signal,
            'signal_strength': strength,
            'total_signals': len(signals),
            'suggested_level': level_suggestion['suggested_level'],
            'level_change': level_suggestion['level_change'],
            'level_reason': level_suggestion['reason'],
            'level_conditions': level_suggestion['conditions'],
            'buy_count': buy_count,
            'pct_change_1d': pct_1d,
            'pct_change_1w': pct_1w,
            'pct_change_1m': pct_1m,
            'pct_from_high_52w': round(pct_from_high, 2),
            'data_status': 'ok',
            'analysis_date': datetime.now().strftime('%Y-%m-%d %H:%M')
        }
    
    def get_signal_emoji(self, signal: str) -> str:
        """Converte segnale in emoji"""
        if signal == 'BUY':
            return '🟢'
        elif signal == 'SELL':
            return '🔴'
        else:
            return '🟡'
    
    def format_analysis_summary(self, analysis: Dict, fund_name: str = '') -> str:
        """Formatta un riassunto dell'analisi per display/email"""
        emoji = self.get_signal_emoji(analysis['final_signal'])
        
        price = analysis.get('current_price')
        ma = analysis.get('ma')
        rsi = analysis.get('rsi')
        pct = analysis.get('pct_from_high_52w')

        summary = f"""
{emoji} {fund_name}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prezzo: €{price:.2f if price else 'N/A'}
MM{self.ma_period}: €{ma:.2f if ma else 'N/A'} ({analysis.get('ma_signal', 'N/A')})
RSI: {rsi:.1f if rsi else 'N/A'} ({analysis.get('rsi_signal', 'N/A')})
MACD: {analysis.get('macd_signal', 'N/A')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SEGNALE: {analysis.get('final_signal', 'N/A')} ({analysis.get('signal_strength', 0)}/{analysis.get('total_signals', 0)} indicatori)
Distanza da Max 52w: {pct:.1f if pct else 'N/A'}%
"""
        return summary


    @staticmethod
    def calculate_signal_purity(fund_prices: pd.Series, bench_prices: pd.Series,
                                 window: int = 90) -> dict:
        """
        Calcola il Signal Purity Score: quanto il fondo segue il suo benchmark.

        R²        — quota di variazione del fondo spiegata dal benchmark (0–1).
        Tracking Error — dispersione annualizzata rispetto al benchmark (%).
        Alpha     — rendimento eccedente annualizzato aggiustato per beta (%).
        Score 0–100 — composito: R²(40pt) + TE(35pt) + alpha(25pt).
        """
        try:
            # Allinea gli indici (rimuovi timezone se presente)
            fund_idx  = pd.to_datetime(fund_prices.index).tz_localize(None).normalize()
            bench_idx = pd.to_datetime(bench_prices.index).tz_localize(None).normalize()
            f = pd.Series(fund_prices.values,  index=fund_idx)
            b = pd.Series(bench_prices.values, index=bench_idx)

            aligned = pd.DataFrame({'fund': f, 'bench': b}).dropna().tail(window)
            if len(aligned) < 20:
                return {'available': False, 'reason': f'Solo {len(aligned)} gg allineati (min 20)'}

            r_fund  = aligned['fund'].pct_change().dropna()
            r_bench = aligned['bench'].pct_change().dropna()
            if len(r_fund) < 15:
                return {'available': False, 'reason': 'Rendimenti insufficienti'}

            corr = r_fund.corr(r_bench)
            r2   = round(float(corr ** 2), 3) if not pd.isna(corr) else 0.0

            var_bench = float(r_bench.var())
            beta = round(float(r_fund.cov(r_bench)) / var_bench, 3) if var_bench > 0 else 1.0

            te    = round(float((r_fund - r_bench).std() * (252 ** 0.5) * 100), 2)
            alpha = round(float((r_fund.mean() - beta * r_bench.mean()) * 252 * 100), 2)

            score_r2    = int(min(r2, 1.0) * 40)
            score_te    = int(max(0.0, (15.0 - te) / 15.0 * 35)) if te <= 15 else 0
            score_alpha = 25 if alpha > 0 else 0
            score       = score_r2 + score_te + score_alpha

            if score >= 65:
                label, color = 'Alta', 'green'
            elif score >= 40:
                label, color = 'Media', 'yellow'
            else:
                label, color = 'Bassa', 'red'

            return {
                'available':       True,
                'r2':              r2,
                'beta':            beta,
                'tracking_error':  te,
                'alpha_ann':       alpha,
                'score':           score,
                'label':           label,
                'color':           color,
                'days_used':       len(aligned),
            }
        except Exception as e:
            return {'available': False, 'reason': str(e)}


def test_analyzer():
    """Test dell'analizzatore tecnico"""
    analyzer = TechnicalAnalyzer()
    
    # Genera dati di test
    np.random.seed(42)
    dates = pd.date_range(end=datetime.now(), periods=100, freq='D')
    prices = pd.Series(100 + np.cumsum(np.random.randn(100) * 2), index=dates)
    
    # Analizza
    result = analyzer.analyze_fund(prices, level=1)
    print(analyzer.format_analysis_summary(result, "Test Fund"))


if __name__ == "__main__":
    test_analyzer()

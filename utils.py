# utils.py

import pandas as pd
import yfinance as yf
import streamlit as st
from datetime import datetime
import numpy as np
import warnings

# Potlačení FutureWarnings (které často generuje yfinance)
warnings.simplefilter(action='ignore', category=FutureWarning)


# --- FUNKCE PRO MAPOVÁNÍ A ZÍSKÁNÍ DAT ---

# Funkce pro mapování XTB symbolů na yfinance tickery a měny (NOVÝ HYBRIDNÍ VZOR)
def get_ticker_and_currency(symbol):
    symbol_upper = symbol.upper()
    
    # === VZOR 1: STATICKÁ MAPA PRO ZNÁMÉ VÝJIMKY (NEJVYŠŠÍ PRIORITA) ===
    # Formát: 'XTB_SYMBOL': ('YFINANCE_TICKER', 'MĚNA')
    TICKER_MAP = {
        # Fixy pro ETF
        'CSPX.UK': ('CSPX.L', 'USD'), 
        'CSPX': ('CSPX.L', 'USD'), 
        'CNDX.UK': ('CNDX.L', 'USD'),
        'CNDX': ('CNDX.L', 'USD'),
        
        # Explicitní fixy pro akcie, které zlobí
        'TUI.DE': ('TUI1.DE', 'EUR'),       # TUI AG: XTB TUI.DE -> YF TUI1.DE
        'STLAM.IT': ('STLA.MI', 'EUR'),    # Stellantis N.V.: XTB STLAM.IT -> YF STLA.MI
    }

    # 1. Nejprve zkontrolujeme explicitní mapování
    if symbol_upper in TICKER_MAP:
        return TICKER_MAP[symbol_upper]
    
    # === VZOR 2: GENERICKÁ PRAVIDLA PRO KONCOVKY (FALLBACK) ===
    
    # 2. Pravidlo pro USA: Odstranit .US nebo .USD (problém s Google)
    if symbol_upper.endswith('.US'):
        return symbol_upper[:-3], 'USD'
    elif symbol_upper.endswith('.USD'):
        # Toto řeší GOOGL.USD
        return symbol_upper[:-4], 'USD'
            
    # 3. Pravidlo pro Německo: Zachovat koncovku .DE
    elif symbol_upper.endswith('.DE'):
        return symbol_upper[:-3] + '.DE', 'EUR'
        
    # 4. Pravidlo pro Itálii: Převést na .MI (Milán)
    elif symbol_upper.endswith('.IT'):
        return symbol_upper[:-3] + '.MI', 'EUR' 
        
    # 5. Pravidlo pro UK: Převést na .L (LSE)
    elif symbol_upper.endswith('.UK'):
        return symbol_upper[:-3] + '.L', 'GBP' 
        
    # 6. Výchozí hodnota
    return symbol, 'USD'


# Funkce pro stažení aktuálních cen (batch processing + Caching)
@st.cache_data(ttl=600)
def get_current_prices(symbols):
    if not symbols:
        return {}
    
    # Použití funkce z utility
    ticker_map = {symbol: get_ticker_and_currency(symbol) for symbol in symbols}
    yf_tickers = [v[0] for v in ticker_map.values()]
    currencies_to_fetch = set(v[1] for v in ticker_map.values() if v[1] != 'USD')
    currency_rates = {'USD': 1.0}
    currency_tickers = [f"{curr}USD=X" for curr in currencies_to_fetch]
    
    # Původní, méně agresivní ošetření chyb pro aktuální ceny
    if currency_tickers:
        try:
            rates_data = yf.download(currency_tickers, period='1d', progress=False)['Close']
            if isinstance(rates_data, pd.Series):
                currency = currency_tickers[0].split('USD=X')[0]
                # Zajištění, že bereme poslední platnou hodnotu
                currency_rates[currency] = rates_data.iloc[-1] if not rates_data.empty and pd.notna(rates_data.iloc[-1]) else 1.0
            else:
                for curr_ticker in currency_tickers:
                    currency = curr_ticker.split('USD=X')[0]
                    # Zajištění, že bereme poslední platnou hodnotu
                    rate = rates_data[curr_ticker].iloc[-1] if not rates_data[curr_ticker].empty and pd.notna(rates_data[curr_ticker].iloc[-1]) else 1.0
                    currency_rates[currency] = rate
        except:
            st.warning("Problém se stažením kurzu, používám výchozí 1.0.")
            pass 
            
    prices = {}
    
    try:
        data = yf.download(yf_tickers, period='1d', progress=False)['Close']
        if isinstance(data, pd.Series): 
            ticker = yf_tickers[0]
            price = data.iloc[-1] if not data.empty and pd.notna(data.iloc[-1]) else 0
            for symbol, (t, curr) in ticker_map.items():
                if t == ticker:
                    prices[symbol] = price * currency_rates.get(curr, 1.0)
                    break
        else: 
            for symbol, (ticker, currency) in ticker_map.items():
                try:
                    price = data[ticker].iloc[-1] if not data[ticker].empty and pd.notna(data[ticker].iloc[-1]) else 0
                    prices[symbol] = price * currency_rates.get(currency, 1.0)
                except (KeyError, IndexError):
                    prices[symbol] = 0
    except:
        st.error("Nepodařilo se stáhnout ceny pro jeden nebo více symbolů (pravděpodobně chyba Yahoo Finance). Používám 0 pro chybějící data.")
        for symbol in symbols:
             prices[symbol] = 0
             
    return prices

# Funkce pro výpočet otevřených pozic (statická data z reportu)
def calculate_positions(transactions):
    positions = {}
    for _, row in transactions.iterrows():
        if pd.isna(row['Symbol']): continue
        symbol = row['Symbol']
        quantity = row['Volume']
        purchase_value = row['Purchase value']
        transaction_type = row['Type']
        if symbol not in positions:
            positions[symbol] = {'quantity': 0, 'total_cost': 0}
        if 'BUY' in transaction_type.upper():
            positions[symbol]['quantity'] += quantity
            positions[symbol]['total_cost'] += purchase_value
    for symbol in positions:
        if positions[symbol]['quantity'] > 0:
            positions[symbol]['avg_price'] = positions[symbol]['total_cost'] / positions[symbol]['quantity']
        else:
            positions[symbol]['avg_price'] = 0
    return {k: v for k, v in positions.items() if v['quantity'] > 0} 

# Historická data (s cachingem) - PŮVODNÍ, FUNKČNÍ LOGIKA
@st.cache_data(ttl=3600)
def get_historical_prices(symbols, start_date, end_date):
    hist_prices = {}
    
    # Získání jedinečných měn k převodu
    currencies = set(get_ticker_and_currency(s)[1] for s in symbols if get_ticker_and_currency(s)[1] != 'USD')
    hist_rates = {}
    currency_tickers = [f"{curr}USD=X" for curr in currencies]
    
    # Stažení historických kurzů měn
    if currency_tickers:
        try:
            rates_df = yf.download(currency_tickers, start=start_date, end=end_date, progress=False)['Close']
            if isinstance(rates_df, pd.Series):
                currency = currency_tickers[0].split('USD=X')[0]
                hist_rates[currency] = rates_df.fillna(method='ffill')
            else:
                for curr in currencies:
                    ticker = f"{curr}USD=X"
                    hist_rates[curr] = rates_df[ticker].fillna(method='ffill')
        except:
            pass # Chyba pri stahování kurzu, ignorujeme a použijeme default USD
            
    for symbol in symbols:
        ticker, currency = get_ticker_and_currency(symbol)
        try:
            # Stažení historických cen akcie/ETF
            df = yf.Ticker(ticker).history(start=start_date, end=end_date) 
            prices = df['Close'].fillna(method='ffill')
            
            # Přepočet do USD pomocí historických kurzů
            if currency != 'USD' and currency in hist_rates:
                rates = hist_rates[currency].reindex(prices.index, method='ffill')
                prices = prices * rates
            
            hist_prices[symbol] = prices
            
        except Exception:
            hist_prices[symbol] = pd.Series()
            
    return hist_prices

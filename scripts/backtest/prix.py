#!/usr/bin/env python3
"""
Étape 2 du backtest : cours quotidiens depuis Yahoo Finance.

Télécharge, pour chaque société ayant au moins 8 exercices dans les fondamentaux et un
ticker actuel : cours ajusté (dividendes réinvestis), cours de clôture ajusté des seules
divisions, volume et divisions d'actions. Ajoute SPY (S&P 500) et le change EUR/USD.

Limite assumée : Yahoo ne fournit pas les cours des sociétés radiées. Celles-ci ne peuvent
donc pas être achetées dans la simulation ; le moteur mesure et publie leur poids.

Sortie : cache/prix.pkl
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

CACHE = Path(__file__).resolve().parents[2] / "cache"
DEBUT = "2012-01-01"
LOT = 150


def tickers_utiles() -> list[str]:
    f = pd.read_csv(CACHE / "fondamentaux.csv.gz", usecols=["cik", "fin"])
    n = f.groupby("cik").fin.nunique()
    ciks = set(n[n >= 8].index.astype(str))
    tick = json.loads((CACHE / "tickers.json").read_text())
    return sorted({t for c, t in tick.items() if c in ciks})


def telecharger(tickers: list[str]) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    blocs = {k: [] for k in ("adj", "close", "volume", "splits")}
    for i in range(0, len(tickers), LOT):
        lot = tickers[i:i + LOT]
        for essai in range(3):
            try:
                df = yf.download(lot, start=DEBUT, auto_adjust=False, actions=True, progress=False,
                                 threads=True, group_by="column")
                break
            except Exception as e:
                print(f"Lot {i}: erreur {e}, nouvel essai")
                time.sleep(30)
        else:
            continue
        if df is None or df.empty:
            continue
        if not isinstance(df.columns, pd.MultiIndex):        # un seul ticker
            df.columns = pd.MultiIndex.from_product([df.columns, lot])
        for cle, col in (("adj", "Adj Close"), ("close", "Close"), ("volume", "Volume"), ("splits", "Stock Splits")):
            if col in df.columns.get_level_values(0):
                blocs[cle].append(df[col])
        print(f"{min(i + LOT, len(tickers))}/{len(tickers)} tickers")
        time.sleep(2)
    out = {}
    for k, v in blocs.items():
        m = pd.concat(v, axis=1) if v else pd.DataFrame()
        m = m.loc[:, ~m.columns.duplicated()]
        m.index = pd.to_datetime(m.index).tz_localize(None)
        out[k] = m.astype("float32")
    return out


def main() -> None:
    tickers = tickers_utiles() + ["SPY", "EURUSD=X"]
    print(f"{len(tickers)} tickers à télécharger")
    prix = telecharger(tickers)
    pd.to_pickle(prix, CACHE / "prix.pkl")
    n = prix["adj"].notna().any().sum() if not prix["adj"].empty else 0
    print(f"Cours obtenus pour {n} tickers sur {len(tickers)}.")


if __name__ == "__main__":
    main()

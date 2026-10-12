#!/usr/bin/env python3
"""
Calcule les indicateurs du backtest et les écrit dans data/backtest.json.

Entrée : data/backtest_courbe.csv, avec trois colonnes
    date,strategie,benchmark
    2016-01-29,100,100
    2016-02-29,101.3,99.2
    ...
(valeurs liquidatives mensuelles ou quotidiennes, dans la même devise,
dividendes réinvestis, frais déjà déduits côté stratégie).

Les textes de data/backtest.json (règles, limites, sensibilité…) sont conservés :
seuls les blocs "metriques", "annuel" et "courbe" sont recalculés.

Usage : python scripts/backtest_metriques.py [--taux-sans-risque 2.0]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"


def metriques(vl: pd.Series, periodes_par_an: float, rf: float) -> dict:
    rend = vl.pct_change().dropna()
    annees = (vl.index[-1] - vl.index[0]).days / 365.25
    cagr = (vl.iloc[-1] / vl.iloc[0]) ** (1 / annees) - 1
    vol = rend.std() * np.sqrt(periodes_par_an)
    sharpe = (cagr - rf) / vol if vol else None
    dd = (vl / vl.cummax() - 1).min()
    return {
        "cagr_pct": round(cagr * 100, 2),
        "volatilite_pct": round(vol * 100, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "drawdown_max_pct": round(dd * 100, 2),
        "perf_totale_pct": round((vl.iloc[-1] / vl.iloc[0] - 1) * 100, 1),
    }


def main() -> None:
    rf = 2.0
    if "--taux-sans-risque" in sys.argv:
        rf = float(sys.argv[sys.argv.index("--taux-sans-risque") + 1])
    rf /= 100

    df = pd.read_csv(DATA / "backtest_courbe.csv", parse_dates=["date"]).set_index("date").sort_index()
    ecart_moyen = df.index.to_series().diff().dt.days.median()
    ppa = 12 if ecart_moyen > 20 else 252

    doc = json.loads((DATA / "backtest.json").read_text(encoding="utf-8"))
    s, b = df["strategie"], df["benchmark"]
    ms, mb = metriques(s, ppa, rf), metriques(b, ppa, rf)

    rs, rb = s.pct_change().dropna(), b.pct_change().dropna()
    ecart = (rs - rb)
    te = ecart.std() * np.sqrt(ppa)
    ms["tracking_error_pct"] = round(te * 100, 2)
    ms["ratio_information"] = round((ms["cagr_pct"] - mb["cagr_pct"]) / 100 / te, 2) if te else None
    ms["mois_surperformance_pct"] = round((ecart > 0).mean() * 100, 1)

    annuel = []
    for an, g in df.groupby(df.index.year):
        prec = df.loc[df.index < g.index[0]]
        depart = prec.iloc[-1] if len(prec) else g.iloc[0]
        annuel.append({
            "annee": int(an),
            "strategie_pct": round((g["strategie"].iloc[-1] / depart["strategie"] - 1) * 100, 1),
            "benchmark_pct": round((g["benchmark"].iloc[-1] / depart["benchmark"] - 1) * 100, 1),
        })

    doc["metriques"] = {"strategie": ms, "benchmark": mb, "taux_sans_risque_pct": rf * 100}
    doc["annuel"] = annuel
    doc["courbe"] = {
        "dates": [d.strftime("%Y-%m-%d") for d in df.index],
        "strategie": [round(v / s.iloc[0] * 100, 2) for v in s],
        "benchmark": [round(v / b.iloc[0] * 100, 2) for v in b],
    }
    doc["periode"] = f"{df.index[0]:%m/%Y} – {df.index[-1]:%m/%Y}"
    (DATA / "backtest.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Backtest {doc['periode']} : CAGR {ms['cagr_pct']} % contre {mb['cagr_pct']} % pour le benchmark.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Met à jour data/marche.json à partir de data/site.json et data/portfolio.json.

- Télécharge les cours de clôture ajustés (dividendes réinvestis) via yfinance.
- Convertit chaque ligne dans la devise du portefeuille (EUR par défaut).
- Recalcule la valeur liquidative jour par jour : chaque ligne est exécutée au
  cours de clôture RÉEL du jour d'exécution (pas au cours saisi à la main),
  avec les frais simulés. C'est ce qui rend le suivi vérifiable.

Usage :
    python scripts/maj_marche.py            # données réelles (GitHub Action)
    python scripts/maj_marche.py --exemple  # données synthétiques de démonstration
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Les actions cotées à Londres sont cotées en pence (GBX) : 1 GBP = 100 GBX.
DIVISEUR = {"GBX": 100.0}
DEVISE_REELLE = {"GBX": "GBP"}

# Ordres de grandeur utilisés uniquement par le mode --exemple.
FX_EXEMPLE = {"USD": 1.12, "CAD": 1.55, "GBP": 0.85, "CHF": 0.94, "SEK": 11.2,
              "DKK": 7.46, "NOK": 11.7, "JPY": 168.0}


def lire(nom: str) -> dict:
    return json.loads((DATA / nom).read_text(encoding="utf-8"))


def devise_reelle(d: str) -> str:
    return DEVISE_REELLE.get(d, d)


def ticker_fx(devise: str, base: str) -> str:
    """Ticker Yahoo donnant le nombre d'unités de `devise` pour 1 unité de `base`."""
    return f"{base}{devise}=X"


# --------------------------------------------------------------------------- #
# Sources de cours
# --------------------------------------------------------------------------- #
def telecharger(tickers: list[str], debut: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(tickers, start=debut, auto_adjust=True, progress=False,
                     threads=True, group_by="column")
    closes = df["Close"]
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(tickers[0])
    closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
    return closes


def synthetique(tickers: list[str], debut: str, ancrages: dict[str, tuple[str, float]],
                base: str) -> pd.DataFrame:
    """Marche aléatoire déterministe, recalée pour passer par le cours d'entrée saisi."""
    rng = np.random.default_rng(7)
    dates = pd.bdate_range(debut, date.today())
    out = {}
    for t in tickers:
        if t.endswith("=X"):
            dev = t[len(base):-2]
            r = rng.normal(0, 0.003, len(dates))
            out[t] = FX_EXEMPLE.get(dev, 1.0) * np.exp(np.cumsum(r))
            continue
        # Lignes : volatilité d'une action ; benchmark (non ancré) : celle d'un indice.
        r = rng.normal(0.0006, 0.013, len(dates)) if t in ancrages else rng.normal(0.0003, 0.007, len(dates))
        serie = 100 * np.exp(np.cumsum(r))
        if t in ancrages:
            d0, prix0 = ancrages[t]
            i0 = dates.searchsorted(pd.Timestamp(d0))
            i0 = min(i0, len(dates) - 1)
            serie = serie / serie[i0] * prix0
        out[t] = serie
    return pd.DataFrame(out, index=dates)


# --------------------------------------------------------------------------- #
# Calcul
# --------------------------------------------------------------------------- #
def premier_jour(index: pd.DatetimeIndex, d: str) -> pd.Timestamp | None:
    i = index.searchsorted(pd.Timestamp(d))
    return index[i] if i < len(index) else None


def calculer(site: dict, portefeuille: dict, closes: pd.DataFrame) -> dict:
    base = site.get("devise_portefeuille", "EUR")
    capital = float(site["capital_initial"])
    frais = float(site.get("frais_transaction_pct", 0.1)) / 100
    bench = site["benchmark_ticker"]
    lancement = pd.Timestamp(site["date_lancement"])
    avertissements: list[str] = []

    jours = pd.bdate_range(min(lancement, closes.index.min()), closes.index.max())
    closes = closes.reindex(closes.index.union(jours)).sort_index().ffill()
    closes = closes.loc[closes.index >= lancement]
    closes = closes.loc[closes.index.isin(jours)]
    idx = closes.index

    def prix_base(p: dict) -> pd.Series | None:
        t = p["ticker"]
        if t not in closes or closes[t].isna().all():
            return None
        s = closes[t] / DIVISEUR.get(p["devise"], 1.0)
        dev = devise_reelle(p["devise"])
        if dev != base:
            fx = ticker_fx(dev, base)
            if fx not in closes:
                return None
            s = s / closes[fx]
        return s

    positions = [p for p in portefeuille.get("positions", []) if p.get("ticker")]
    evenements = []  # (jour, ordre, type, position)
    series = {}
    for p in positions:
        s = prix_base(p)
        if s is None:
            avertissements.append(f"Pas de cours pour {p['ticker']} : ligne ignorée.")
            continue
        series[p["ticker"]] = s
        j = premier_jour(idx, p["date_entree"])
        if j is None:
            avertissements.append(f"{p['ticker']} : date d'entrée postérieure aux dernières données.")
            continue
        evenements.append((j, 1, "achat", p))
        if p.get("statut") == "cloturee" and p.get("date_sortie"):
            js = premier_jour(idx, p["date_sortie"])
            if js is not None:
                evenements.append((js, 0, "vente", p))
    evenements.sort(key=lambda e: (e[0], e[1]))

    cash = capital
    titres: dict[str, float] = {}
    infos: dict[str, dict] = {}
    vl = pd.Series(index=idx, dtype=float)
    k = 0
    for jour in idx:
        while k < len(evenements) and evenements[k][0] == jour:
            _, _, typ, p = evenements[k]
            t = p["ticker"]
            px = series[t].loc[jour]
            loc = closes[t].loc[jour]
            if typ == "achat":
                montant = capital * float(p.get("poids_cible", 5)) / 100
                montant = min(montant, cash)
                titres[t] = montant * (1 - frais) / px
                cash -= montant
                saisi = float(p.get("cours_entree") or 0)
                infos[t] = {"cours_execution": round(float(loc), 4),
                            "jour_execution": jour.strftime("%Y-%m-%d"),
                            "ecart_saisie_pct": round((saisi / loc - 1) * 100, 2) if saisi else None,
                            "investi": montant}
                if saisi and abs(saisi / loc - 1) > 0.02:
                    avertissements.append(
                        f"{t} : cours saisi ({saisi}) éloigné de plus de 2 % de la clôture réelle ({loc:.2f}).")
            else:
                cash += titres.pop(t, 0.0) * px * (1 - frais)
                infos[t]["cours_sortie_marche"] = round(float(loc), 4)
                infos[t]["jour_sortie"] = jour.strftime("%Y-%m-%d")
            k += 1
        vl.loc[jour] = cash + sum(n * series[t].loc[jour] for t, n in titres.items())

    dernier = idx[-1]
    valeur = float(vl.iloc[-1])
    sortie_lignes = {}
    for p in positions:
        t = p["ticker"]
        if t not in infos:
            continue
        inf = infos[t]
        exec_loc = inf["cours_execution"]
        ouverte = t in titres
        fin_loc = float(closes[t].loc[dernier]) if ouverte else inf.get("cours_sortie_marche", exec_loc)
        jour_fin = dernier if ouverte else pd.Timestamp(inf["jour_sortie"])
        jour_exec = pd.Timestamp(inf["jour_execution"])
        perf_base = float(series[t].loc[jour_fin] / series[t].loc[jour_exec] - 1)
        valeur_ligne = titres[t] * float(series[t].loc[dernier]) if ouverte else 0.0
        sortie_lignes[t] = {
            **{k2: v for k2, v in inf.items() if k2 != "investi"},
            "cours_actuel": round(float(closes[t].loc[dernier]), 4),
            "perf_locale_pct": round((fin_loc / exec_loc - 1) * 100, 2),
            "perf_base_pct": round(perf_base * 100, 2),
            "contribution_pct": round(inf["investi"] * perf_base / capital * 100, 2),
            "valeur": round(valeur_ligne, 2),
            "poids_actuel_pct": round(valeur_ligne / valeur * 100, 2) if ouverte else 0.0,
        }

    b = closes[bench] if bench in closes else None
    if b is None:
        avertissements.append(f"Pas de cours pour le benchmark {bench}.")
    rend = vl.pct_change().dropna()
    vol = float(rend.std() * math.sqrt(252)) if len(rend) > 5 else None
    dd = float((vl / vl.cummax() - 1).min())
    nb_jours = (dernier - idx[0]).days
    perf = valeur / capital - 1
    perf_b = float(b.iloc[-1] / b.iloc[0] - 1) if b is not None else None

    return {
        "exemple": False,
        "maj": dernier.strftime("%Y-%m-%d"),
        "devise": base,
        "synthese": {
            "valeur": round(valeur, 2),
            "perf_pct": round(perf * 100, 2),
            "perf_benchmark_pct": round(perf_b * 100, 2) if perf_b is not None else None,
            "volatilite_pct": round(vol * 100, 2) if vol is not None else None,
            "drawdown_max_pct": round(dd * 100, 2),
            "cash_pct": round(cash / valeur * 100, 2),
            "nb_lignes": len(titres),
            "jours": nb_jours,
        },
        "positions": sortie_lignes,
        "courbe": {
            "dates": [d.strftime("%Y-%m-%d") for d in idx],
            "portefeuille": [round(v / capital * 100, 3) for v in vl],
            "benchmark": [round(float(v / b.iloc[0] * 100), 3) for v in b] if b is not None else [],
        },
        "avertissements": avertissements,
    }


def main() -> None:
    exemple = "--exemple" in sys.argv
    site = lire("site.json")
    portefeuille = lire("portfolio.json")
    base = site.get("devise_portefeuille", "EUR")
    positions = [p for p in portefeuille.get("positions", []) if p.get("ticker")]

    tickers = sorted({p["ticker"] for p in positions} | {site["benchmark_ticker"]})
    devises = sorted({devise_reelle(p["devise"]) for p in positions} - {base})
    tickers += [ticker_fx(d, base) for d in devises]
    debut = min([site["date_lancement"]] + [p["date_entree"] for p in positions])
    debut = (pd.Timestamp(debut) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")

    if exemple:
        ancrages = {p["ticker"]: (p["date_entree"], float(p["cours_entree"])) for p in positions}
        closes = synthetique(tickers, debut, ancrages, base)
    else:
        closes = telecharger(tickers, debut)

    resultat = calculer(site, portefeuille, closes)
    resultat["exemple"] = exemple
    (DATA / "marche.json").write_text(json.dumps(resultat, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    s = resultat["synthese"]
    print(f"Mis à jour au {resultat['maj']} : performance {s['perf_pct']} % "
          f"(benchmark {s['perf_benchmark_pct']} %), {s['nb_lignes']} lignes.")
    for a in resultat["avertissements"]:
        print("AVERTISSEMENT :", a)


if __name__ == "__main__":
    main()

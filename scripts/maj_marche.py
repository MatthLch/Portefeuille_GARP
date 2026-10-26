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
    # Événements : achat initial, renforcements, allègements, sortie totale.
    # Ordre dans une même séance : ventes (0) avant achats (1).
    evenements = []  # (jour, ordre, type, position, quantité, cours saisi, date saisie)
    series = {}
    for p in positions:
        t = p["ticker"]
        s = prix_base(p)
        if s is None:
            avertissements.append(f"Pas de cours pour {t} : ligne ignorée.")
            continue
        series[t] = s
        j = premier_jour(idx, p["date_entree"])
        if j is None:
            avertissements.append(f"{t} : date d'entrée postérieure aux dernières données.")
            continue
        evenements.append((j, 1, "achat", p, float(p.get("poids_cible") or 4.5), p.get("cours_entree"), "Entrée"))
        for m in p.get("mouvements") or []:
            if not m.get("date"):
                continue
            jm = premier_jour(idx, m["date"])
            if jm is None:
                avertissements.append(f"{t} : mouvement du {m['date']} postérieur aux dernières données.")
                continue
            if m.get("type") == "Renforcement":
                evenements.append((jm, 1, "achat", p, float(m.get("poids_pct") or 0), m.get("cours"), "Renforcement"))
            elif m.get("type") == "Allègement":
                evenements.append((jm, 0, "vente", p, float(m.get("poids_pct") or 0), m.get("cours"), "Allègement"))
        if p.get("statut") == "cloturee" and p.get("date_sortie"):
            js = premier_jour(idx, p["date_sortie"])
            if js is not None:
                evenements.append((js, 0, "vente", p, 100.0, p.get("cours_sortie"), "Sortie"))
    evenements.sort(key=lambda e: (e[0], e[1]))

    cash = capital
    titres: dict[str, float] = {}
    infos: dict[str, dict] = {}
    vl = pd.Series(index=idx, dtype=float)
    k = 0
    for jour in idx:
        while k < len(evenements) and evenements[k][0] == jour:
            _, _, typ, p, qte, saisi, libelle = evenements[k]
            t = p["ticker"]
            px = float(series[t].loc[jour])
            loc = float(closes[t].loc[jour])
            inf = infos.setdefault(t, {"achats": 0.0, "ventes": 0.0, "cout_base": 0.0, "cout_local": 0.0,
                                       "mouvements": []})
            if saisi and float(saisi) and abs(float(saisi) / loc - 1) > 0.02:
                avertissements.append(f"{t} ({libelle.lower()}) : cours saisi ({saisi}) éloigné de plus de 2 % "
                                      f"de la clôture réelle ({loc:.2f}).")
            if typ == "achat":
                montant = min(capital * qte / 100, cash)
                n = montant * (1 - frais) / px
                titres[t] = titres.get(t, 0.0) + n
                cash -= montant
                inf["achats"] += montant
                inf["cout_base"] += montant
                inf["cout_local"] += n * loc
                inf.setdefault("jour_execution", jour.strftime("%Y-%m-%d"))
                inf.setdefault("cours_execution", round(loc, 4))
                inf["mouvements"].append({"date": jour.strftime("%Y-%m-%d"), "type": libelle,
                                          "cours": round(loc, 4), "poids_pct": round(qte, 2)})
            else:
                detenu = titres.get(t, 0.0)
                if detenu <= 0:
                    avertissements.append(f"{t} : {libelle.lower()} sans position ouverte, ignoré.")
                    k += 1
                    continue
                f = min(max(qte, 0.0), 100.0) / 100
                vendu = detenu * f
                produit = vendu * px * (1 - frais)
                cash += produit
                inf["ventes"] += produit
                inf["cout_base"] *= (1 - f)
                inf["cout_local"] *= (1 - f)
                titres[t] = detenu - vendu
                if titres[t] <= 1e-9 or f >= 1:
                    titres.pop(t, None)
                    inf["cours_sortie_marche"] = round(loc, 4)
                    inf["jour_sortie"] = jour.strftime("%Y-%m-%d")
                inf["mouvements"].append({"date": jour.strftime("%Y-%m-%d"), "type": libelle,
                                          "cours": round(loc, 4), "part_vendue_pct": round(f * 100, 1)})
            k += 1
        vl.loc[jour] = cash + sum(n * series[t].loc[jour] for t, n in titres.items())

    dernier = idx[-1]
    valeur = float(vl.iloc[-1])
    sortie_lignes = {}
    for p in positions:
        t = p["ticker"]
        if t not in infos or "jour_execution" not in infos[t]:
            continue
        inf = infos[t]
        ouverte = t in titres
        n = titres.get(t, 0.0)
        valeur_ligne = n * float(series[t].loc[dernier]) if ouverte else 0.0
        cours_actuel = float(closes[t].loc[dernier])
        # Prix de revient unitaire (méthode du coût moyen), en devise de cotation.
        pru = inf["cout_local"] / n if ouverte and n else None
        if pru is None:
            achats_loc = [m for m in inf["mouvements"] if m["type"] in ("Entrée", "Renforcement")]
            pru = sum(m["cours"] * m["poids_pct"] for m in achats_loc) / max(sum(m["poids_pct"] for m in achats_loc), 1e-9)
        fin_loc = cours_actuel if ouverte else inf.get("cours_sortie_marche", pru)
        gain = valeur_ligne + inf["ventes"] - inf["achats"]
        sortie_lignes[t] = {
            "jour_execution": inf["jour_execution"],
            "cours_execution": inf["cours_execution"],
            "pru": round(pru, 4),
            "nb_mouvements": len(inf["mouvements"]),
            "mouvements": inf["mouvements"],
            **({"cours_sortie_marche": inf["cours_sortie_marche"], "jour_sortie": inf["jour_sortie"]}
               if not ouverte and "jour_sortie" in inf else {}),
            "cours_actuel": round(cours_actuel, 4),
            "perf_locale_pct": round((fin_loc / pru - 1) * 100, 2) if pru else None,
            "perf_base_pct": round(gain / inf["achats"] * 100, 2) if inf["achats"] else None,
            "contribution_pct": round(gain / capital * 100, 2),
            "investi_pct": round(inf["achats"] / capital * 100, 2),
            "valeur": round(valeur_ligne, 2),
            "poids_actuel_pct": round(valeur_ligne / valeur * 100, 2) if ouverte else 0.0,
        }

    b = closes[bench] if bench in closes else None
    if b is not None:
        # Séance manquante ou cours absent le jour du lancement : on reprend le cours connu le plus proche.
        b = b.ffill().bfill()
        if b.isna().all():
            b = None
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
        # Yahoo renvoie parfois une colonne vide lors d'un téléchargement groupé : on retente seul.
        vides = [t for t in tickers if t not in closes or closes[t].isna().all()]
        for t in vides:
            try:
                c1 = telecharger([t], debut)
                if t in c1 and c1[t].notna().any():
                    closes = closes.drop(columns=[t], errors="ignore").join(c1[[t]], how="outer")
                    print(f"{t} : cours récupérés au second essai.")
                else:
                    print(f"{t} : toujours aucun cours au second essai.")
            except Exception as e:  # noqa: BLE001
                print(f"{t} : échec du second essai ({e}).")

    resultat = calculer(site, portefeuille, closes)
    resultat["exemple"] = exemple
    def propre(o):
        """Remplace NaN et infinis par null : le navigateur refuse un JSON qui en contient."""
        if isinstance(o, dict):
            return {k: propre(v) for k, v in o.items()}
        if isinstance(o, list):
            return [propre(v) for v in o]
        if isinstance(o, float) and not math.isfinite(o):
            return None
        return o

    resultat = propre(resultat)
    (DATA / "marche.json").write_text(json.dumps(resultat, ensure_ascii=False, indent=1, allow_nan=False),
                                      encoding="utf-8")
    s = resultat["synthese"]
    print(f"Mis à jour au {resultat['maj']} : performance {s['perf_pct']} % "
          f"(benchmark {s['perf_benchmark_pct']} %), {s['nb_lignes']} lignes.")
    for a in resultat["avertissements"]:
        print("AVERTISSEMENT :", a)


if __name__ == "__main__":
    main()

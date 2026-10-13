#!/usr/bin/env python3
"""
Test de bout en bout sans internet : fabrique un companyfacts.zip, une table de tickers
et des cours synthétiques au format attendu, dans le dossier cache/ du dépôt indiqué.

    python scripts/backtest/test_synthetique.py <racine_du_depot_de_test>

Cas couverts : sociétés de qualité, pièges (marges instables, capex lourd, levier,
dilution, absence d'EBIT), retraitement ultérieur d'un exercice (le premier chiffre publié
doit être retenu), société radiée sans ticker, division d'actions 4 pour 1.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)
racine = Path(sys.argv[1])
cache = racine / "cache"
cache.mkdir(parents=True, exist_ok=True)

N = 320
TYPES = ["qualite"] * 110 + ["moyenne"] * 110 + ["instable"] * 30 + ["capex"] * 20 + ["levier"] * 20 + ["dilution"] * 15 + ["financiere"] * 15
ANNEES = list(range(2005, 2026))


def fait(val, fin, depot, debut=None, form="10-K"):
    f = {"end": fin, "val": float(val), "filed": depot, "form": form, "fp": "FY", "fy": int(depot[:4]), "accn": f"0000-{depot}"}
    if debut:
        f["start"] = debut
    return f


tickers, info = {}, {}
with zipfile.ZipFile(cache / "companyfacts.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for i in range(N):
        cik, typ = 100000 + i, TYPES[i]
        premiere = 2005 if i % 3 else 2009
        ans = [a for a in ANNEES if a >= premiere]
        rev0 = rng.uniform(2e9, 2e10)
        g = {"qualite": 0.10, "moyenne": 0.05, "instable": 0.04, "capex": 0.06, "levier": 0.05, "dilution": 0.07, "financiere": 0.05}[typ]
        m0 = {"qualite": 0.30, "moyenne": 0.12, "instable": 0.15, "capex": 0.20, "levier": 0.15, "dilution": 0.18, "financiere": 0.25}[typ]
        serie = {}
        rev = rev0
        for k, a in enumerate(ans):
            rev = rev * (1 + g + rng.normal(0, 0.03)) if k else rev0
            marge = m0 + rng.normal(0, 0.01)
            if typ == "instable":
                marge = m0 * (0.2 if a >= 2018 else 1.0) + rng.normal(0, 0.03)
            ebit = rev * marge
            assets = rev * (0.9 if typ != "capex" else 2.5)
            dette = assets * (0.15 if typ != "levier" else 0.85)
            serie[a] = dict(rev=rev, cogs=rev * (0.45 if typ == "qualite" else 0.65), ebit=ebit, ni=ebit * 0.75,
                            pretax=ebit * 0.95, tax=ebit * 0.2, cfo=ebit * 0.95 + rev * 0.03,
                            capex=rev * (0.04 if typ != "capex" else 0.35), da=rev * 0.03, interest=dette * 0.04,
                            shares=4e8 * (1.06 if typ == "dilution" else 1.0) ** k, eps=ebit * 0.75 / (4e8 * (1.06 if typ == "dilution" else 1.0) ** k),
                            assets=assets, cl=rev * 0.2, gw=assets * (0.3 if i % 4 == 0 else 0.0), equity=assets * 0.5,
                            cash=rev * 0.1, dette=dette)
        gaap = {}

        def ajout(tag, unite, f):
            gaap.setdefault(tag, {"units": {unite: []}})["units"][unite].append(f)

        for a in ans:
            if a < 2007:
                continue
            depot_annee = max(a + 1, 2010)
            depot = f"{depot_annee}-02-25"
            # Chaque 10-K rapporte l'exercice et les deux précédents (flux), l'exercice et le précédent (bilan).
            for b in [x for x in ans if a - 2 <= x <= a]:
                d = serie[b]
                facteur = 1.10 if (b == 2015 and a == 2017) else 1.0      # retraitement publié plus tard
                deb, fin = f"{b}-01-01", f"{b}-12-31"
                ajout("Revenues", "USD", fait(d["rev"] * facteur, fin, depot, deb))
                ajout("CostOfRevenue", "USD", fait(d["cogs"], fin, depot, deb))
                if typ != "financiere":
                    ajout("OperatingIncomeLoss", "USD", fait(d["ebit"], fin, depot, deb))
                ajout("NetIncomeLoss", "USD", fait(d["ni"], fin, depot, deb))
                ajout("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "USD", fait(d["pretax"], fin, depot, deb))
                ajout("IncomeTaxExpenseBenefit", "USD", fait(d["tax"], fin, depot, deb))
                ajout("NetCashProvidedByUsedInOperatingActivities", "USD", fait(d["cfo"], fin, depot, deb))
                ajout("PaymentsToAcquirePropertyPlantAndEquipment", "USD", fait(d["capex"], fin, depot, deb))
                ajout("DepreciationDepletionAndAmortization", "USD", fait(d["da"], fin, depot, deb))
                ajout("InterestExpense", "USD", fait(d["interest"], fin, depot, deb))
                ajout("WeightedAverageNumberOfDilutedSharesOutstanding", "shares", fait(d["shares"], fin, depot, deb))
                ajout("EarningsPerShareDiluted", "USD/shares", fait(d["eps"], fin, depot, deb))
                if b >= a - 1:
                    for tag, cle in (("Assets", "assets"), ("LiabilitiesCurrent", "cl"), ("Goodwill", "gw"),
                                     ("StockholdersEquity", "equity"), ("CashAndCashEquivalentsAtCarryingValue", "cash"),
                                     ("LongTermDebt", "dette")):
                        ajout(tag, "USD", fait(d[cle], fin, depot))
        dei = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            fait(serie[a]["shares"], f"{max(a + 1, 2010)}-02-10", f"{max(a + 1, 2010)}-02-25") for a in ans if a >= 2007]}}}
        doc = {"cik": cik, "entityName": f"Société {i:03d} ({typ})", "facts": {"dei": dei, "us-gaap": gaap}}
        z.writestr(f"CIK{cik:010d}.json", json.dumps(doc))
        if i != 5:                                        # la société 5 est radiée : pas de ticker actuel
            tickers[str(i)] = {"cik_str": cik, "ticker": f"T{i:03d}", "title": doc["entityName"]}
        info[f"T{i:03d}"] = (typ, serie[ans[-1]]["shares"], serie[2012]["rev"] if 2012 in serie else rev0)

(cache / "company_tickers.json").write_text(json.dumps(tickers))

jours = pd.bdate_range("2012-01-02", "2026-10-09")
adj, close, vol, splits = {}, {}, {}, {}
for t, (typ, sh, rev12) in info.items():
    mu = {"qualite": 0.13, "moyenne": 0.07}.get(typ, 0.03) / 252
    r = rng.normal(mu, 0.018, len(jours))
    p0 = rev12 * 2.0 / sh
    s = p0 * np.exp(np.cumsum(r))
    sp = np.zeros(len(jours))
    if t == "T010":                                       # division 4 pour 1 en 2020
        k = jours.searchsorted(pd.Timestamp("2020-06-01"))
        sp[k] = 4.0
        s[:k] /= 4.0                                      # Yahoo publie un historique ajusté des divisions
    adj[t], close[t], vol[t], splits[t] = s, s, np.full(len(jours), 2e6), sp
adj["SPY"] = close["SPY"] = 200 * np.exp(np.cumsum(rng.normal(0.10 / 252, 0.011, len(jours))))
adj["EURUSD=X"] = close["EURUSD=X"] = 1.12 + np.cumsum(rng.normal(0, 0.002, len(jours)))
for d in (vol, splits):
    d["SPY"] = d["EURUSD=X"] = np.zeros(len(jours))
prix = {k: pd.DataFrame(v, index=jours).astype("float32") for k, v in
        (("adj", adj), ("close", close), ("volume", vol), ("splits", splits))}
pd.to_pickle(prix, cache / "prix.pkl")
print(f"Données synthétiques écrites dans {cache}")

#!/usr/bin/env python3
"""
Étape 1 du backtest : fondamentaux annuels point-in-time depuis SEC EDGAR.

Source : companyfacts.zip (toutes les données XBRL déposées à la SEC, sociétés radiées
comprises). Pour chaque exercice, on retient la valeur publiée dans le PREMIER dépôt qui
l'a rapportée, et la date de ce dépôt : c'est la date à laquelle l'information était
réellement disponible. Les retraitements ultérieurs sont ignorés.

Sortie : cache/fondamentaux.csv.gz (une ligne par société et par exercice)
         cache/tickers.json      (correspondance CIK → ticker actuel)

Usage :
    SEC_USER_AGENT="Prénom Nom adresse@exemple.com" python scripts/backtest/fondamentaux.py
    python scripts/backtest/fondamentaux.py --zip chemin/companyfacts.zip --tickers chemin/tickers.json
"""
from __future__ import annotations

import gzip
import io
import json
import os
import sys
import time
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

CACHE = Path(__file__).resolve().parents[2] / "cache"
URL_FACTS = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
URL_TICKERS = "https://www.sec.gov/files/company_tickers.json"

# Flux annuels (durée d'environ un an). Ordre = priorité entre balises équivalentes.
FLUX = {
    "rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
            "SalesRevenueGoodsNet", "SalesRevenueServicesNet"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices"],
    "gp": ["GrossProfit"],
    "ebit": ["OperatingIncomeLoss"],
    "ni": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "tax": ["IncomeTaxExpenseBenefit"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
           "DepreciationAndAmortization", "Depreciation"],
    "interest": ["InterestExpense", "InterestExpenseDebt"],
    "shares_dil": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"],
}
# Soldes de bilan (date de clôture de l'exercice).
STOCKS = {
    "assets": ["Assets"],
    "cl": ["LiabilitiesCurrent"],
    "gw": ["Goodwill"],
    "intang": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
    "sti": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesCurrent"],
    "debt_total": ["LongTermDebt"],                        # inclut la part à moins d'un an
    "debt_nc": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "debt_c": ["LongTermDebtCurrent", "DebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "stb": ["ShortTermBorrowings", "CommercialPaper"],
}
COLONNES = ["cik", "nom", "fin", "publie"] + list(FLUX) + list(STOCKS) + ["shares_cover"]


def agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if "@" not in ua:
        sys.exit("SEC_USER_AGENT doit contenir un nom et une adresse e-mail (exigence de la SEC).")
    return ua


def telecharger(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": agent(), "Accept-Encoding": "identity"})
    for essai in range(4):
        try:
            with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
                while True:
                    bloc = r.read(1 << 20)
                    if not bloc:
                        break
                    f.write(bloc)
            return
        except Exception as e:                      # réseau instable : on réessaie
            print(f"Téléchargement interrompu ({e}), nouvel essai…")
            time.sleep(10 * (essai + 1))
    sys.exit(f"Échec du téléchargement de {url}")


def jours(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def premier_depot(faits: list[dict], flux: bool) -> dict[str, tuple[float, str]]:
    """{fin d'exercice: (valeur, date de dépôt)} en gardant le dépôt le plus ancien."""
    out: dict[str, tuple[float, str]] = {}
    for f in faits:
        if not str(f.get("form", "")).startswith("10-K"):
            continue
        fin, depot = f.get("end"), f.get("filed")
        if not fin or not depot:
            continue
        if flux:
            if not f.get("start") or not 350 <= jours(f["start"], fin) <= 380:
                continue
        elif f.get("start"):
            continue
        if fin not in out or depot < out[fin][1]:
            out[fin] = (float(f["val"]), depot)
    return out


def unites(concept: dict) -> list[dict]:
    u = concept.get("units", {})
    for cle in ("USD", "shares", "USD/shares"):
        if cle in u:
            return u[cle]
    return next(iter(u.values()), [])


def extraire(doc: dict) -> list[dict]:
    gaap = doc.get("facts", {}).get("us-gaap", {})
    if not gaap:
        return []
    series: dict[str, dict[str, tuple[float, str]]] = {}
    for cle, balises in list(FLUX.items()) + list(STOCKS.items()):
        flux = cle in FLUX
        fusion: dict[str, tuple[float, str]] = {}
        for b in balises:                              # la première balise disponible l'emporte
            if b in gaap:
                for fin, v in premier_depot(unites(gaap[b]), flux).items():
                    fusion.setdefault(fin, v)
        series[cle] = fusion

    # Exercices = dates de clôture où un flux principal existe.
    fins = set(series["rev"]) | set(series["ebit"]) | set(series["ni"])
    if not fins:
        return []
    cover = doc.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding")
    cover_faits = [f for f in unites(cover) if str(f.get("form", "")).startswith("10-K")] if cover else []

    lignes = []
    for fin in sorted(fins):
        depots = [s[fin][1] for k, s in series.items() if k in ("rev", "ebit", "ni") and fin in s]
        publie = min(depots)
        ligne = {"cik": int(doc.get("cik", 0)), "nom": doc.get("entityName", ""), "fin": fin, "publie": publie}
        for k, s in series.items():
            ligne[k] = s[fin][0] if fin in s else None
        # Actions en circulation en page de garde du 10-K qui a publié l'exercice.
        cands = [f for f in cover_faits if f.get("filed") == publie]
        ligne["shares_cover"] = float(cands[-1]["val"]) if cands else None
        lignes.append(ligne)
    return lignes


def main() -> None:
    CACHE.mkdir(exist_ok=True)
    args = sys.argv[1:]
    zip_path = Path(args[args.index("--zip") + 1]) if "--zip" in args else CACHE / "companyfacts.zip"
    tick_path = Path(args[args.index("--tickers") + 1]) if "--tickers" in args else CACHE / "company_tickers.json"
    if not zip_path.exists():
        print("Téléchargement de companyfacts.zip (environ 1 Go)…")
        telecharger(URL_FACTS, zip_path)
    if not tick_path.exists():
        telecharger(URL_TICKERS, tick_path)

    brut = json.loads(tick_path.read_text())
    tickers: dict[str, str] = {}
    for v in brut.values():                         # format SEC : {"0": {"cik_str":…, "ticker":…}}
        tickers.setdefault(str(int(v["cik_str"])), str(v["ticker"]).replace(".", "-"))
    (CACHE / "tickers.json").write_text(json.dumps(tickers))

    n, n_soc = 0, 0
    with zipfile.ZipFile(zip_path) as z, gzip.open(CACHE / "fondamentaux.csv.gz", "wt", encoding="utf-8") as out:
        import csv
        w = csv.DictWriter(out, fieldnames=COLONNES)
        w.writeheader()
        noms = [x for x in z.namelist() if x.endswith(".json")]
        for i, nom in enumerate(noms):
            try:
                doc = json.loads(z.read(nom))
            except Exception:
                continue
            lignes = extraire(doc)
            if lignes:
                n_soc += 1
                for l in lignes:
                    w.writerow(l)
                    n += 1
            if i % 2000 == 0:
                print(f"{i}/{len(noms)} fichiers lus, {n_soc} sociétés, {n} exercices")
    print(f"Terminé : {n_soc} sociétés, {n} exercices, {len(tickers)} tickers actuels.")


if __name__ == "__main__":
    main()

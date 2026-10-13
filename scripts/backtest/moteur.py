#!/usr/bin/env python3
"""
Étape 3 du backtest : simulation de la couche quantitative de la stratégie.

À chaque fin de trimestre t, avec uniquement les comptes publiés avant t :
  1. Univers investissable : au moins 8 exercices, capitalisation ≥ 1 Md€, volume quotidien
     moyen ≥ 3 M€ sur trois mois, cours disponible.
  2. Règles R1 à R4, filtres du screener, gates G1 à G3, vetos calculés V5 et V6.
  3. Portefeuille équipondéré des sociétés retenues, conservé jusqu'au trimestre suivant.

Comparaisons : univers investissable équipondéré (isole l'apport des règles de qualité),
S&P 500 (SPY), variante avec dimensionnement par phase de momentum, variante concentrée
sur 15 lignes. Sensibilité des résultats aux seuils de G1, G2 et G3.

Sortie : data/backtest.json (blocs chiffrés ; les textes existants sont conservés)
         data/backtest_courbe.csv
"""
from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)

RACINE = Path(__file__).resolve().parents[2]
CACHE = RACINE / "cache"
DATA = RACINE / "data"

FRAIS = 0.001                 # 0,10 % par transaction
TAUX_SANS_RISQUE = 0.02
CAP_MIN_EUR = 1_000e6
VOL_MIN_EUR = 3e6
ANS_MIN = 8
FRAICHEUR_JOURS = 470         # au-delà, le dernier exercice publié est trop ancien


@dataclass(frozen=True)
class Seuils:
    r1: float = 0.40
    derive: float = 0.35      # R2 et G2
    r3: float = 0.25
    roce10: float = 0.09
    om10: float = 0.05
    gm10: float = 0.25
    cagr10: float = 0.04
    fcf_bas: float = -0.30
    fcf_haut_screener: float = 3.20
    g1_roic_adj: float = 0.11
    g1_roce10: float = 0.18
    g3_haut: float = 2.50


# --------------------------------------------------------------------------- #
# Fondamentaux → indicateurs par fenêtre de 10 exercices
# --------------------------------------------------------------------------- #
def charger_fondamentaux() -> pd.DataFrame:
    f = pd.read_csv(CACHE / "fondamentaux.csv.gz", parse_dates=["fin", "publie"])
    f = f.sort_values(["cik", "fin"]).drop_duplicates(["cik", "fin"], keep="first")
    num = lambda c: pd.to_numeric(f[c], errors="coerce")
    f["gp"] = num("gp").fillna(num("rev") - num("cogs"))
    dette = num("debt_total")
    dette = dette.where(dette.notna(), num("debt_nc").fillna(0) + num("debt_c").fillna(0))
    f["dette"] = dette.fillna(0) + num("stb").fillna(0)
    f["tresorerie"] = num("cash").fillna(0) + num("sti").fillna(0)
    taux = (num("tax") / num("pretax")).where(num("pretax") > 0)
    f["taux_is"] = taux.clip(0, 0.5).fillna(0.25)
    f["ebitda"] = num("ebit") + num("da").fillna(0)
    return f


def _cagr(a: float, b: float, ans: float) -> float:
    if not (a > 0 and b > 0 and ans > 0):
        return np.nan
    return (b / a) ** (1 / ans) - 1


def indicateurs(f: pd.DataFrame) -> pd.DataFrame:
    sortie = []
    for cik, g in f.groupby("cik", sort=False):
        g = g.reset_index(drop=True)
        if len(g) < ANS_MIN:
            continue
        a = {c: g[c].to_numpy(dtype=float) for c in
             ["rev", "gp", "ebit", "ni", "cfo", "capex", "assets", "cl", "gw", "intang", "equity",
              "dette", "tresorerie", "taux_is", "ebitda", "interest", "shares_dil", "eps", "shares_cover"]}
        fins = g["fin"].to_numpy()
        publies = g["publie"].to_numpy()
        for i in range(ANS_MIN - 1, len(g)):
            lo = max(0, i - 9)
            s = slice(lo, i + 1)
            rev, ebit = a["rev"][s], a["ebit"][s]
            ok = ~np.isnan(rev) & ~np.isnan(ebit) & (rev > 0)
            if ok.sum() < ANS_MIN:
                continue
            om = np.where(ok, ebit / np.where(rev > 0, rev, np.nan), np.nan)
            om10 = np.nanmean(om)
            om3 = np.nanmean(om[-3:])
            derive = abs(om3 - om10) / abs(om10) if om10 and not np.isnan(om10) else np.nan
            gm = a["gp"][s] / np.where(rev > 0, rev, np.nan)
            gm10 = np.nanmean(gm) if np.isfinite(gm).sum() >= ANS_MIN else np.nan
            ce = a["assets"][s] - np.nan_to_num(a["cl"][s])
            roce = np.where(ce > 0, ebit / ce, np.nan)
            roce10 = np.nanmean(roce) if np.isfinite(roce).sum() >= 5 else np.nan
            ic = a["equity"][s] + a["dette"][s] - a["tresorerie"][s]
            roic = np.where(ic > 0, ebit * (1 - a["taux_is"][s]) / ic, np.nan)
            roic5 = np.nanmean(roic[-5:]) if np.isfinite(roic[-5:]).sum() >= 3 else np.nan
            actif = a["assets"][i]
            gwa = (np.nan_to_num(a["gw"][i]) / actif) if actif and actif > 0 else 0.0
            roic_adj = roic5 / (1 - gwa) if gwa < 0.95 else np.nan
            idx = np.where(ok)[0]
            ans = (fins[lo + idx[-1]] - fins[lo + idx[0]]) / np.timedelta64(1, "D") / 365.25
            cagr10 = _cagr(rev[idx[0]], rev[idx[-1]], ans)
            fcf = a["cfo"][s] - np.nan_to_num(a["capex"][s])
            ni = a["ni"][s]
            m = ~np.isnan(fcf) & ~np.isnan(ni)
            fcf_ni = fcf[m].sum() / ni[m].sum() if m.sum() >= 5 and ni[m].sum() > 0 else np.nan
            inc = np.nan_to_num(a["gw"][i]) + np.nan_to_num(a["intang"][i])
            r1 = (a["rev"][i] / actif) / (1 - inc / actif) if actif and actif > 0 and inc < actif else np.nan
            r3 = np.nanmean(np.nan_to_num(a["capex"][s][-3:]) / np.where(rev[-3:] > 0, rev[-3:], np.nan))
            r4 = np.nansum(ebit[-5:] > 0) >= 4
            # V5 : dilution
            sh = a["shares_dil"][s]
            j0 = len(rev) - 6 if len(rev) >= 6 else 0
            dsh = _cagr(sh[j0], sh[-1], 5) if len(sh) >= 6 else np.nan
            deps = _cagr(a["eps"][s][j0], a["eps"][s][-1], 5)
            drev = _cagr(rev[j0], rev[-1], 5)
            v5 = bool(dsh > 0.02 and (np.isnan(deps) or deps <= drev)) if not np.isnan(dsh) else False
            # V6 : continuité
            nd = a["dette"][i] - a["tresorerie"][i]
            ebd = a["ebitda"][i]
            it = a["interest"][i]
            capex_i = np.nan_to_num(a["capex"][i])
            v6 = bool((ebd <= 0 and nd > 0) or (ebd > 0 and nd / ebd > 4.0))
            if it and it > 0 and not np.isnan(it):
                v6 = v6 or ebit[-1] / it < 2.0 or (ebd - capex_i) / it < 1.5
            actions = a["shares_cover"][i] if not np.isnan(a["shares_cover"][i]) else a["shares_dil"][i]
            sortie.append({
                "cik": int(cik), "nom": g["nom"].iat[i], "fin": fins[i], "dispo": publies[lo:i + 1].max(),
                "n_ans": int(ok.sum()), "om10": om10, "derive": derive, "gm10": gm10, "roce10": roce10,
                "roic_adj": roic_adj, "cagr10": cagr10, "fcf_ni": fcf_ni, "r1": r1, "r3": r3, "r4": bool(r4),
                "v5": v5, "v6": v6, "dsh5": dsh, "actions": actions,
            })
    return pd.DataFrame(sortie)


def qualite(m: pd.DataFrame, s: Seuils) -> pd.Series:
    regles = (m.r1 >= s.r1) & (m.derive <= s.derive) & (m.r3 <= s.r3) & m.r4
    screener = ((m.roce10 >= s.roce10) & (m.om10 >= s.om10) & (m.gm10.isna() | (m.gm10 >= s.gm10))
                & (m.cagr10 >= s.cagr10) & m.fcf_ni.between(s.fcf_bas, s.fcf_haut_screener))
    gates = (((m.roic_adj >= s.g1_roic_adj) | (m.roce10 >= s.g1_roce10)) & (m.derive <= s.derive)
             & m.fcf_ni.between(s.fcf_bas, s.g3_haut))
    return (regles & screener & gates & ~m.v5 & ~m.v6).fillna(False).astype(bool)


def prescore(m: pd.DataFrame) -> pd.Series:
    """Approximation mécanique du pré-score : moyenne des rangs centiles des ancrages disponibles."""
    rangs = [m.roic_adj.rank(pct=True), m.om10.rank(pct=True), (-m.derive).rank(pct=True),
             m.cagr10.rank(pct=True), (-m.dsh5.fillna(0)).rank(pct=True)]
    return pd.concat(rangs, axis=1).mean(axis=1)


# --------------------------------------------------------------------------- #
# Marché
# --------------------------------------------------------------------------- #
class Marche:
    def __init__(self, prix: dict[str, pd.DataFrame]):
        self.adj = prix["adj"].sort_index()
        self.close = prix["close"].reindex(self.adj.index)
        vol = prix["volume"].reindex(self.adj.index)
        sp = prix["splits"].reindex(self.adj.index).fillna(0).replace(0, 1)
        fac = sp.iloc[::-1].cumprod().iloc[::-1].shift(-1).fillna(1)   # divisions postérieures à la date
        self.brut = self.close * fac
        self.dollar_vol = (self.close * vol).rolling(63, min_periods=40).mean()
        self.ma50 = self.adj.rolling(50, min_periods=50).mean()
        self.ma200 = self.adj.rolling(200, min_periods=200).mean()
        self.pente = self.ma200 / self.ma200.shift(20) - 1
        fx = self.adj["EURUSD=X"] if "EURUSD=X" in self.adj else pd.Series(1.1, index=self.adj.index)
        self.eurusd = fx.ffill().fillna(1.1)
        mens = self.adj.resample("ME").last()
        r = mens.pct_change(fill_method=None)
        # Après la dernière cotation (radiation, rachat), la ligne est supposée passée en liquidités.
        self.rend_m = r.where(mens.notna() | mens.ffill().isna(), 0.0)

    def ligne(self, df: pd.DataFrame, t: pd.Timestamp) -> pd.Series:
        sub = df.loc[:t]
        return sub.iloc[-1] if len(sub) else pd.Series(dtype=float)

    def phase(self, t: pd.Timestamp) -> pd.Series:
        c, m50, m200, p = (self.ligne(x, t) for x in (self.adj, self.ma50, self.ma200, self.pente))
        ph = pd.Series("?", index=c.index)
        ph[(m50 > m200)] = "P"
        ph[(m50 <= m200)] = "C"
        ph[(c > m50) & (m50 > m200) & (p > 0)] = "A"
        ph[(c < m50) & (m50 < m200) & (p < 0)] = "B"
        ph[m200.isna()] = "?"
        return ph


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #
def simuler(poids_par_date: dict[pd.Timestamp, pd.Series], rend: pd.DataFrame) -> tuple[pd.Series, float]:
    mois = rend.index[(rend.index > min(poids_par_date))]
    dates_reb = sorted(poids_par_date)
    w = pd.Series(dtype=float)
    vl, val, rotations = [], 1.0, []
    k = 0
    for mo in mois:
        cout = 0.0
        while k < len(dates_reb) and dates_reb[k] < mo:
            cible = poids_par_date[dates_reb[k]]
            tous = w.index.union(cible.index)
            delta = (cible.reindex(tous).fillna(0) - w.reindex(tous).fillna(0)).abs().sum()
            cout += delta * FRAIS
            rotations.append(delta / 2)
            w = cible.copy()
            k += 1
        r = rend.loc[mo].reindex(w.index).fillna(0.0) if len(w) else pd.Series(dtype=float)
        rp = float((w * r).sum()) if len(w) else 0.0
        val *= (1 + rp) * (1 - cout)
        if len(w):
            w = w * (1 + r) / (1 + rp) if (1 + rp) != 0 else w
        vl.append((mo, val))
    s = pd.Series(dict(vl))
    return s, (float(np.mean(rotations[1:])) if len(rotations) > 1 else 0.0)


def metriques(vl: pd.Series) -> dict:
    r = vl.pct_change().dropna()
    ans = (vl.index[-1] - vl.index[0]).days / 365.25
    cagr = (vl.iloc[-1] / vl.iloc[0]) ** (1 / ans) - 1
    vol = r.std() * math.sqrt(12)
    return {"cagr_pct": round(cagr * 100, 2), "volatilite_pct": round(vol * 100, 2),
            "sharpe": round((cagr - TAUX_SANS_RISQUE) / vol, 2) if vol else None,
            "drawdown_max_pct": round(float((vl / vl.cummax() - 1).min()) * 100, 2),
            "perf_totale_pct": round((vl.iloc[-1] / vl.iloc[0] - 1) * 100, 1)}


def main() -> None:
    print("Indicateurs…")
    fond = charger_fondamentaux()
    ind = indicateurs(fond)
    tick = {int(k): v for k, v in json.loads((CACHE / "tickers.json").read_text()).items()}
    ind["ticker"] = ind.cik.map(tick)
    print(f"{ind.cik.nunique()} sociétés avec au moins {ANS_MIN} exercices")

    print("Marché…")
    mk = Marche(pd.read_pickle(CACHE / "prix.pkl"))
    fins_mois = mk.rend_m.index
    trimestres = [d for d in fins_mois if d.month in (3, 6, 9, 12)]

    base = Seuils()
    variantes_seuils = {
        "G1 · ROIC ajusté minimal": [("9 %", replace(base, g1_roic_adj=0.09)), ("11 % (retenu)", base), ("13 %", replace(base, g1_roic_adj=0.13))],
        "G2 · Dérive de marge maximale": [("0,25", replace(base, derive=0.25)), ("0,35 (retenu)", base), ("0,45", replace(base, derive=0.45))],
        "G3 · Borne haute FCF / RN": [("200 %", replace(base, g3_haut=2.0)), ("250 % (retenu)", base), ("300 %", replace(base, g3_haut=3.0))],
    }
    tous_seuils = {base} | {s for v in variantes_seuils.values() for _, s in v}

    portefeuilles: dict[str, dict] = {"univers": {}, "momentum": {}, "concentre": {}}
    par_seuils: dict[Seuils, dict] = {s: {} for s in tous_seuils}
    diag = []
    ind = ind.sort_values("dispo")
    for t in trimestres:
        dispo = ind[ind.dispo <= t].groupby("cik").tail(1)
        dispo = dispo[(t - dispo.fin).dt.days <= FRAICHEUR_JOURS]
        if dispo.empty:
            continue
        fx = float(mk.eurusd.loc[:t].iloc[-1])
        brut, dv = mk.ligne(mk.brut, t), mk.ligne(mk.dollar_vol, t)
        d = dispo.copy()
        d["cap_usd"] = d.actions * d.ticker.map(brut)
        d["dv_usd"] = d.ticker.map(dv)
        d["cote"] = d.ticker.notna() & d.ticker.map(brut).notna()
        investissable = d[d.cote & (d.cap_usd >= CAP_MIN_EUR * fx) & (d.dv_usd >= VOL_MIN_EUR * fx)]
        if len(investissable) < 100:
            continue
        portefeuilles["univers"][t] = pd.Series(1 / len(investissable), index=investissable.ticker.unique())
        for s in tous_seuils:
            q = investissable[qualite(investissable, s)]
            if len(q):
                par_seuils[s][t] = pd.Series(1 / q.ticker.nunique(), index=q.ticker.unique())
        q = investissable[qualite(investissable, base)]
        if len(q):
            ph = mk.phase(t).reindex(q.ticker).fillna("?")
            brut_w = ph.map({"A": 1.0, "P": 1 / 3, "C": 1 / 3, "B": 0.0, "?": 1 / 3})
            if brut_w.sum() > 0:
                portefeuilles["momentum"][t] = brut_w[brut_w > 0] / brut_w.sum()
            top = q.assign(ps=prescore(q)).nlargest(15, "ps")
            portefeuilles["concentre"][t] = pd.Series(1 / len(top), index=top.ticker.unique())
        # Mesure du biais du survivant : sociétés qui passent les règles de qualité sans cours disponible.
        qual_tous = d[qualite(d, base)]
        diag.append({"date": t, "univers": len(investissable), "retenues": len(q),
                     "qualite_sans_cours": int((~qual_tous.cote).sum()), "qualite_total": len(qual_tous)})

    principal = par_seuils[base]
    debut = min(principal)
    print(f"Simulation de {debut:%m/%Y} à {fins_mois[-1]:%m/%Y}…")
    rend = mk.rend_m.loc[debut:]
    vl_strat, rot = simuler(principal, rend)
    vl_univ, _ = simuler(portefeuilles["univers"], rend)
    vl_mom, _ = simuler(portefeuilles["momentum"], rend)
    vl_conc, rot_c = simuler(portefeuilles["concentre"], rend)
    spy = (1 + rend.loc[rend.index > debut, "SPY"].fillna(0)).cumprod() if "SPY" in rend else None
    idx = vl_strat.index
    courbe = pd.DataFrame({"strategie": vl_strat, "benchmark": vl_univ.reindex(idx)})
    courbe = pd.concat([pd.DataFrame({"strategie": [1.0], "benchmark": [1.0]}, index=[debut]), courbe])
    courbe = courbe / courbe.iloc[0] * 100

    def met(vl):
        vl = pd.concat([pd.Series([1.0], index=[debut]), vl.reindex(idx)])
        return metriques(vl)

    ms, mb = met(vl_strat), met(vl_univ)
    rs, rb = courbe.strategie.pct_change().dropna(), courbe.benchmark.pct_change().dropna()
    te = (rs - rb).std() * math.sqrt(12)
    ms["tracking_error_pct"] = round(te * 100, 2)
    ms["ratio_information"] = round((ms["cagr_pct"] - mb["cagr_pct"]) / 100 / te, 2) if te else None
    ms["mois_surperformance_pct"] = round(((rs - rb) > 0).mean() * 100, 1)

    annuel = []
    for an, g in courbe.groupby(courbe.index.year):
        prec = courbe[courbe.index < g.index[0]]
        dep = prec.iloc[-1] if len(prec) else g.iloc[0]
        annuel.append({"annee": int(an),
                       "strategie_pct": round((g.strategie.iloc[-1] / dep.strategie - 1) * 100, 1),
                       "benchmark_pct": round((g.benchmark.iloc[-1] / dep.benchmark - 1) * 100, 1)})

    comparaisons = [
        {"nom": "Règles de qualité, équipondéré", "role": "principal", **ms},
        {"nom": "Univers investissable, équipondéré", "role": "benchmark", **mb},
    ]
    if spy is not None:
        comparaisons.append({"nom": "S&P 500 (SPY)", "role": "reference", **met(spy.reindex(idx))})
    comparaisons += [
        {"nom": "Règles de qualité + dimensionnement par phase", "role": "variante", **met(vl_mom)},
        {"nom": "15 lignes, pré-score approché", "role": "variante", **met(vl_conc)},
    ]
    sensibilite = []
    for nom, liste in variantes_seuils.items():
        cagr = []
        for _, s in liste:
            vl, _ = simuler(par_seuils[s], rend) if par_seuils[s] else (None, 0)
            cagr.append(met(vl)["cagr_pct"] if vl is not None else None)
        sensibilite.append({"parametre": nom, "valeurs": [v for v, _ in liste], "cagr_pct": cagr})

    dg = pd.DataFrame(diag)
    dg = dg[dg.date >= debut]
    couverture = {
        "lignes_moyennes": round(float(dg.retenues.mean()), 1),
        "univers_moyen": round(float(dg.univers.mean()), 0),
        "part_qualite_sans_cours_pct": round(float(dg.qualite_sans_cours.sum() / max(dg.qualite_total.sum(), 1) * 100), 1),
        "rotation_trimestrielle_pct": round(rot * 100, 1),
        "rotation_concentre_pct": round(rot_c * 100, 1),
    }

    chemin = DATA / "backtest.json"
    doc = json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else {}
    doc.update({
        "exemple": False,
        "periode": f"{debut:%m/%Y} – {idx[-1]:%m/%Y}",
        "nom_strategie": "Règles de qualité",
        "nom_benchmark": "Univers investissable",
        "devise": "USD",
        "metriques": {"strategie": ms, "benchmark": mb, "taux_sans_risque_pct": TAUX_SANS_RISQUE * 100},
        "comparaisons": comparaisons,
        "annuel": annuel,
        "sensibilite": sensibilite,
        "couverture": couverture,
        "courbe": {"dates": [d.strftime("%Y-%m-%d") for d in courbe.index],
                   "strategie": [round(v, 2) for v in courbe.strategie],
                   "benchmark": [round(v, 2) for v in courbe.benchmark]},
    })
    chemin.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    courbe.rename_axis("date").to_csv(DATA / "backtest_courbe.csv", float_format="%.3f")
    print(json.dumps({"periode": doc["periode"], "strategie": ms, "benchmark": mb, "couverture": couverture},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

# Portefeuille modèle Quality Growth / GARP

**Site : [matthlch.github.io/Portefeuille_GARP](https://matthlch.github.io/Portefeuille_GARP/)**

Portefeuille modèle fictif, lancé le 22 octobre 2026 avec un capital de 100 000 €, qui applique mécaniquement une stratégie de sélection de sociétés de qualité en croissance (*compounders*), achetées lorsque le cours implique un rendement supérieur à un rendement exigé.

Le site présente les positions et leur performance, le journal daté de chaque décision, la méthode complète, la démarche de calibrage et un backtest hors échantillon.

> Portefeuille fictif à visée de démonstration. Ce n'est ni un conseil en investissement ni une recommandation d'achat ou de vente.

---

## La stratégie en bref

Le processus comporte sept étages. Chacun réduit l'univers, et seuls les derniers font appel au jugement. Le prix n'est jamais regardé avant que la qualité ait été établie.

| Étage | Contenu |
|---|---|
| 1. Univers | 15 pays, capitalisation ≥ 1 Md€, liquidité > 3 M€/jour. Les exclusions se font par nature économique (règles R1 à R4) et non par secteur GICS |
| 2. Gates | Rentabilité (ROIC ajusté du goodwill), stabilité de la marge, qualité du cash |
| 3. Pré-score | Classement automatique, qui donne la file de travail |
| 4. Vetos | Six vetos binaires : passif juridique, opacité comptable, trésorerie, dépendance client, dilution, continuité |
| 5. Score Gem | Quatre piliers notés sur 5 : moat, allocation du capital, piste de réinvestissement, transparence comptable |
| 6. Reverse DCF | Rendement implicite au cours actuel, comparé à un rendement exigé de 8 %, 9 % ou 10 % selon la qualité |
| 7. Momentum | Phase de tendance hebdomadaire, qui dimensionne la position sans autoriser l'achat |

## Pourquoi le suivi est vérifiable

- **Horodatage** : chaque position et chaque entrée de journal est un commit daté dans ce dépôt. L'historique Git montre qu'aucune décision n'a été modifiée après coup.
- **Cours non saisis** : la performance est calculée chaque soir à partir des cours de clôture réels (Yahoo Finance), et non à partir de cours saisis à la main.
- **Frais simulés** : chaque transaction supporte 0,10 % de frais.
- **Indice de référence** : la performance est comparée à l'iShares Core MSCI World (EUR).

## Contenu du dépôt

```
index.html                  le site (page unique, sans dépendance serveur)
data/portfolio.json         positions
data/journal.json           journal des décisions
data/marche.json            cours et performance, recalculés automatiquement
data/backtest.json          résultats du backtest
scripts/maj_marche.py       calcul quotidien de la performance
scripts/backtest/           backtest sur les fondamentaux SEC EDGAR (sociétés américaines, 2015-2026)
.github/workflows/          tâches automatiques (mise à jour quotidienne, backtest)
```

Hébergement : GitHub Pages. Saisie : Pages CMS. Calculs : Python (pandas, yfinance) exécuté par GitHub Actions.

## Contact

Matthieu Lochey · [LinkedIn](https://www.linkedin.com/in/matthieu-lochey)

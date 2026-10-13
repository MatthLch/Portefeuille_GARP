# Portefeuille modèle GARP — guide de mise en ligne

Site vitrine statique : portefeuille modèle, journal des décisions, backtest, méthode, à propos.
Hébergement gratuit sur GitHub Pages, administration via Pages CMS, cours mis à jour chaque soir par une GitHub Action.

```
index.html                     le site (une seule page, cinq onglets)
data/site.json                 profil et réglages (nom, benchmark, seuils…)
data/portfolio.json            positions            ← modifié via l'admin
data/journal.json              journal des décisions ← modifié via l'admin
data/marche.json               cours et performance  ← généré automatiquement, ne pas modifier
data/backtest.json             textes et indicateurs du backtest
data/backtest_courbe.csv       valeurs liquidatives du backtest (date,strategie,benchmark)
scripts/maj_marche.py          recalcul quotidien du portefeuille
scripts/backtest/             backtest EDGAR (fondamentaux, cours, moteur)
.pages.yml                     configuration de l'interface d'administration
.github/workflows/maj-cours.yml  tâche automatique quotidienne
```

Toutes les données fournies sont des **exemples** (bandeau « Données d'exemple » en haut du site).

---

## 1. Créer le dépôt GitHub (10 min)

1. Crée un compte sur [github.com](https://github.com) si besoin. Choisis un identifiant sobre : il apparaîtra dans l'adresse du site.
2. Bouton **New repository** → nom : `portefeuille-garp` → **Public** (obligatoire pour GitHub Pages gratuit) → **Create repository**.
3. Sur la page du dépôt vide, clique sur **uploading an existing file**.
4. Dans le Finder, ouvre le dossier `portefeuille-garp` décompressé. Les fichiers commençant par un point (`.github`, `.pages.yml`, `.nojekyll`) sont masqués par macOS : appuie sur **Cmd + Maj + .** pour les afficher.
5. Sélectionne **tout le contenu** du dossier (pas le dossier lui-même) et glisse-le dans la page GitHub → **Commit changes**.
6. Vérifie que `.github/workflows/maj-cours.yml` et `.pages.yml` apparaissent bien dans le dépôt. Si ce n'est pas le cas, recommence l'envoi pour ces fichiers.

> Alternative plus confortable pour la suite : l'application gratuite **GitHub Desktop**.

## 2. Mettre le site en ligne (2 min)

1. Dans le dépôt : **Settings → Pages**.
2. **Source** : *Deploy from a branch* → branche `main`, dossier `/ (root)` → **Save**.
3. Une à deux minutes plus tard, le site est en ligne à l'adresse `https://<ton-identifiant>.github.io/portefeuille-garp/`.

C'est **le lien à donner aux recruteurs**. Il est en lecture seule : aucune modification n'est possible depuis le site.

## 3. Activer la mise à jour automatique des cours (2 min)

1. **Settings → Actions → General** → *Workflow permissions* : coche **Read and write permissions** → **Save**.
2. Onglet **Actions** → *Mise à jour des cours* → **Run workflow**.
3. Au bout d'une minute environ, un commit « Cours au … » apparaît et `data/marche.json` contient les vrais cours.

Ensuite, le recalcul tourne seul du lundi au vendredi à 22h17 UTC, et immédiatement après chaque modification d'une position.

**Point important :** le script exécute chaque ligne au **cours de clôture réel** du jour d'exécution, et non au cours saisi à la main. Si les deux s'écartent de plus de 2 %, un avertissement apparaît dans le journal de l'Action. C'est ce qui rend le suivi vérifiable.

## 4. Ton accès administrateur (5 min)

1. Va sur [app.pagescms.org](https://app.pagescms.org) → **Sign in with GitHub**.
2. Autorise l'application et installe-la **uniquement sur le dépôt `portefeuille-garp`**.
3. Ouvre le dépôt : trois rubriques apparaissent, **Positions**, **Journal des décisions** et **Profil et réglages**.

Chaque enregistrement crée un commit horodaté dans GitHub. L'onglet Journal du site renvoie vers cet historique dès que le champ « Dépôt GitHub » est renseigné : c'est ta preuve qu'aucune décision n'a été réécrite.

**Passer une nouvelle position :**
1. *Positions* → ajouter un élément → remplir ticker Yahoo, cours, valeur intrinsèque, notes Gem, absence de veto, phase de momentum, thèse et conditions d'invalidation → **Save**.
2. *Journal des décisions* → ajouter une entrée de type « Entrée » qui résume la décision → **Save**.
3. Le site affiche la ligne immédiatement ; les cours et la performance arrivent après le recalcul automatique (une à deux minutes).

**Clôturer une position :** passe son statut à `cloturee`, renseigne la date, le cours et le motif de sortie, puis ajoute une entrée « Sortie » au journal. Ne supprime jamais une position clôturée.

**Trouver le bon ticker :** cherche la société sur [finance.yahoo.com](https://finance.yahoo.com) et copie le symbole exact. Exemples : `CSU.TO` (Toronto), `HLMA.L` (Londres, cotée en pence → devise `GBX`), `NOVO-B.CO` (Copenhague), `6861.T` (Tokyo), `ADYEN.AS` (Amsterdam), `SAP.DE` (Xetra), `MSFT` (États-Unis).

## 5. Remplacer les données d'exemple

1. *Profil et réglages* : nom, présentation, LinkedIn, lien du CV, **date de lancement** (le jour de ta première vraie décision), dépôt GitHub (`identifiant/portefeuille-garp`).
2. *Positions* : supprime les exemples, ajoute tes positions, **décoche « Données d'exemple »**.
3. *Journal des décisions* : supprime les exemples, ajoute une note de lancement, **décoche « Données d'exemple »**.
4. Backtest : voir la section 6. Tant que `"exemple": true` figure dans `data/backtest.json`, le bandeau d'avertissement reste affiché.

## 6. Lancer le backtest (SEC EDGAR)

Le backtest applique la couche quantitative de la stratégie (R1 à R4, filtres du screener, gates G1 à G3, vetos V5 et V6) à toutes les sociétés américaines, avec les comptes tels qu'ils étaient publiés à chaque date. Il tourne dans une GitHub Action, en 1 à 2 heures.

1. **Identification SEC.** La SEC exige un nom et une adresse e-mail dans chaque requête. Dans le dépôt : **Settings → Secrets and variables → Actions → New repository secret**. Nom : `SEC_USER_AGENT`. Valeur : `Prénom Nom ton-adresse@exemple.com`. Le secret n'est jamais visible publiquement.
2. **Onglet Actions → Backtest EDGAR → Run workflow.**
3. À la fin, un commit « Backtest EDGAR du … » met à jour `data/backtest.json` et `data/backtest_courbe.csv`. L'onglet Backtest du site affiche les vrais résultats, et le bandeau « Données d'exemple » disparaît pour cette partie.

Les étapes, dans `scripts/backtest/` :

| Script | Rôle |
|---|---|
| `fondamentaux.py` | Télécharge la base XBRL de la SEC et garde, pour chaque exercice, le chiffre du premier dépôt et sa date |
| `prix.py` | Télécharge les cours quotidiens Yahoo (dividendes réinvestis, volumes, divisions) |
| `moteur.py` | Applique les règles à chaque fin de trimestre et simule les portefeuilles |
| `test_synthetique.py` | Fabrique des données factices pour tester toute la chaîne sans internet |

Les textes de l'onglet (périmètre, règles, limites) se modifient dans `data/backtest.json` ; le moteur ne réécrit que les blocs chiffrés.

Si l'étape 2 échoue (Yahoo limite parfois les requêtes), relance simplement l'Action.

## 7. Voir le site sur ton Mac avant publication

Le site lit ses données avec `fetch`, ce qu'un navigateur refuse quand on ouvre simplement `index.html` depuis le Finder. Lance donc un petit serveur local :

```bash
cd ~/Downloads/portefeuille-garp      # adapte le chemin
python3 -m http.server 8000
```

puis ouvre `http://localhost:8000`. Pour voir des cours synthétiques sans internet : `python3 scripts/maj_marche.py --exemple`.

## 8. Options

**Nom de domaine (environ 10 €/an).** Achète par exemple `matthieu-lochey.fr` chez un registrar (OVH, Gandi…). Dans **Settings → Pages → Custom domain**, saisis le domaine, puis crée chez le registrar les enregistrements DNS indiqués par GitHub. Coche ensuite **Enforce HTTPS**.

**Savoir quel recruteur a consulté le site.** Crée un compte gratuit sur [goatcounter.com](https://www.goatcounter.com) avec un code (par exemple `mlochey`), puis saisis ce code dans *Profil et réglages*. Donne ensuite à chaque candidature un lien distinct :
`https://…/portefeuille-garp/?ref=oddo`, `…/?ref=amundi`. Le tableau de bord GoatCounter affiche chaque `ref` ouvert et sa date. L'outil ne dépose aucun cookie et ne requiert donc pas de bandeau de consentement.

**Modifier le texte de la méthode.** Il se trouve dans `index.html`, fonction `renderMethode()`. Les seuils (marge de sécurité, score Gem) et le nom des piliers se règlent dans *Profil et réglages*.

## 9. Liste de contrôle avant de diffuser le lien

- [ ] Feu vert de la compliance sur une publication personnelle relative aux marchés.
- [ ] Aucune valeur suivie par ton employeur, ni en portefeuille ni dans le journal.
- [ ] Bandeau « Données d'exemple » disparu (`exemple: false` dans les trois fichiers concernés).
- [ ] Aucun export de fournisseur payant republié tel quel : uniquement tes propres calculs.
- [ ] Le dépôt est public : n'y mets rien de confidentiel (le CV en PDF peut y figurer, pas de document interne).
- [ ] Ouvre le site sur ton téléphone pour vérifier l'affichage.

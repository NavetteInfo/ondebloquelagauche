# On débloque la gauche

Site statique plus un petit serveur Python. Le serveur relit trois adresses publiques en GET, refait le merge, et sert les fiches à jour.

- `https://onbloquelextremedroite.fun/api/counts`
- `https://onbloquelextremedroite.fun/api/accounts`
- `https://onbloquelextremedroite.fun/api/submissions`

Le join se fait sur `réseau:id`. Les profils qui ne sont pas en `https` sur un réseau connu sont écartés. Rien n'est demandé à personne : pas de blocage, pas d'écriture, pas de harcèlement.

## Lancer

```bash
python3 server.py
```

Le site écoute sur le port 8080. Le premier relevé part tout de suite, puis toutes les 5 minutes. Chaque passage lit `/api/counts`, `/api/accounts` et `/api/submissions` en GET, avec l'en-tête `X-BF: 1`. La page interroge `/api/status` et recharge `data/fiches.json` quand `as_of` change.

```bash
HOST=0.0.0.0 PORT=8080 REFRESH_SECONDS=300 python3 server.py
```

`REFRESH_SECONDS` ne descend pas sous 300. Cette boucle ne part que si quelqu'un lance `server.py`. Rien ne le relance.

La copie GitHub Pages est mise à jour autrement. L'action `Relevé` part toutes les 15 minutes, aux minutes 8, 23, 38 et 53 UTC, fait un seul `python3 refresh.py`, puis pousse le résultat. Ce push relance le build. Sans changement dans les fichiers publiés, elle ne pousse rien.

Les fichiers `deck_*.json` déjà déposés ne couvrent qu'une partie de la liste. `python3 refresh.py --deck` les empile sur la base déjà publiée, écrit `data/merged.json`, et refait les fiches. Le chiffre d'une fiche vient d'abord d'un `fcounts_*.json` déjà déposé. Un fichier qui demande une session est ignoré. Un relevé avec un total et une carte vide reste valide : le total global est affiché, et les fiches ne sont pas remises à zéro. S'il n'y a pas de chiffre pour une fiche, la carte deck sert, sinon le dernier chiffre publié. Ce passage ne lit pas `/api/counts`. Une carte d'au moins 1 000 clés, avec un total, réaffiche ce total et ses chiffres. Sans total, le nombre affiché reste la somme des fiches. À côté du total, `day_delta` donne l'écart avec le relevé daté le plus proche de 24 h plus tôt, pris dans `data/historique/counts` et les `fcounts`, et au moins 12 h avant. La page affiche la vraie durée et l'heure de ce relevé. Chaque fiche porte aussi `day_gain` : son chiffre moins celui du `data/fiches.json` publié le plus près de cette même heure, lu dans l'historique git. Une tranche ajoute ou met à jour ses fiches. Elle ne retire pas celles qui manquent. L'action `Deck` fait ce passage toutes les heures, à la minute 17 UTC. Elle ne lit pas `/api/accounts`. Sur la machine qui reçoit les JSON, `deck-hourly.sh` fait le même passage à la minute 12 UTC, puis pousse.

Un seul passage, sans serveur :

```bash
python3 refresh.py
```

Le dernier passage reste dans `data/raw/` (non versionné). Chaque réponse JSON est aussi gardée telle qu'elle est arrivée, dans `data/historique/` (versionné) : un fichier par passage, nommé à l'heure de Paris, sous `accounts/`, `counts/` et, quand la lecture renvoie du JSON, `submissions/`. Un fichier déjà écrit avec les mêmes octets n'est pas réécrit. Si le dernier fichier de ce type a déjà ces octets, le passage n'en ajoute pas un autre. Le merge publié est `data/fiches.json`. La page source affiche aussi `accounts.js` et `socials.js` : ces fiches, dont Marine Le Pen, ne sont pas dans `/api/accounts`. Leur compteur est dans `/api/counts`, sous le handle. Le merge les ajoute. Un retrait publié dans `/api/accounts` les retire. `/api/submissions` est lu en GET seulement, et le résultat tient dans `data/submissions.json`. Rien n'est proposé, rien n'est envoyé. Le serveur qui tourne ajoute ces fichiers à chaque passage. L'action horaire des abonnés ne les pousse pas.

`data/comparaison.json` compare les pulls de `data/historique`, puis ceux de `PULLS_DIR`, sinon `/pulls`, sinon le dossier `pulls` à côté du dépôt. Le relevé brut du site est ajouté s'il est plus récent ou différent. `python3 pulls.py` reconstruit ce fichier. Sans aucune de ces copies, le fichier déjà publié reste en place. La comparaison ne publie que les écarts. Le serveur Python rend la comparaison, pas les JSON datés. La copie GitHub Pages sert les fichiers du dépôt, donc aussi `data/historique/`.

## Docker

```bash
docker build -t ondebloquelagauche .
docker run --rm -p 8080:8080 ondebloquelagauche
```

## Adresse publique

`SITE_URL` réécrit le canonical, Open Graph, la carte Twitter et le sitemap. Le serveur n'utilise pas l'en-tête Host. Sans variable valide, ces adresses restent `https://navetteinfo.github.io/ondebloquelagauche`. En production, fixe l'adresse réelle, en `http` ou `https`, sans chemin :

```bash
SITE_URL=https://exemple.fr python3 server.py
```

La carte est `media/card.jpg` (1200×630). Les balises `twitter:` et `og:` sont avant la politique de sécurité. Une URL déjà partagée sur X reste en cache environ 7 jours : `?v=2` force une nouvelle lecture. Le `robots.txt` du dépôt autorise `Twitterbot`. X lit celui de `https://navetteinfo.github.io/robots.txt`, qui reste une 404 sans site utilisateur.

Seuls `GET` et `HEAD` passent. Le reste reçoit un 405 et compte dans la même file. Le serveur ne rend pas les chemins en point (dont `.git`), `data/raw/`, `data/historique/`, ni le code. Le relevé public est `data/fiches.json`. Les liens vers les profils partent sans Referer.

## File d'attente

Pas de répartiteur de charge. Deux requêtes passent en même temps. Douze autres attendent, vingt secondes au plus. Chaque adresse a 16 jetons, qui reviennent au rythme de 0,25 par seconde. Au-delà, la connexion attend, puis reçoit `429` et `Retry-After`.

```bash
MAX_INFLIGHT=2 MAX_QUEUE=12 QUEUE_WAIT=20 IP_CAPACITY=16 IP_REFILL=0.25 IP_WAIT=12 python3 server.py
```

## Ce qui reste affiché si la source refuse

Cloudflare peut renvoyer une page au lieu du JSON. En recovery, `/api/counts` et `/api/accounts` répondent `404` tant que la lecture n'envoie pas l'en-tête `X-BF` de la page publique. Avec cet en-tête, les deux JSON passent. Une lecture qui cale est retentée une fois. Le serveur garde la dernière copie valide et `/api/status` signale un relevé raté.

`GET /api/submissions` reste fermé pendant la maintenance (`Page indisponible pendant la maintenance.`). Rien n'est envoyé en POST. La page n'affiche plus ce guichet. Les ajouts et les retraits déjà versés dans `/api/accounts` restent dans l'onglet Pulls.

Les abonnés viennent de `data/abonnes.json`. `python3 refresh.py --followers` relit leur `followers.js` et réécrit le chiffre de chaque fiche. Si `followers.js` ne répond plus (410 depuis le 29 octobre 2026), il lit `https://lepodium2027.fr/data/classement.json` (CC BY 4.0). Ce classement ne suit que des candidats et des partis : il met à jour les comptes déjà connus, et les autres gardent leur dernier chiffre. Le serveur le fait au démarrage, puis toutes les heures (`FOLLOWERS_SECONDS`, plancher 3600). L'action `.github/workflows/abonnes.yml` fait la même chose pour la copie GitHub Pages. L'écart reste calé sur le relevé du 28 octobre 2026 à 11:16 UTC, et seulement si aucun des deux chiffres n'est arrondi au millier. Il n'entre pas dans le total des blocages. Ce passage ne touche pas `/api/counts`.

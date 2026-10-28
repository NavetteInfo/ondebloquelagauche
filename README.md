# On débloque la gauche

Site statique plus un petit serveur Python. Le serveur relit deux adresses publiques, refait le merge, et sert les fiches à jour.

- `https://onbloquelextremedroite.fun/api/counts`
- `https://onbloquelextremedroite.fun/api/accounts`

Le join se fait sur `réseau:id`. Les profils qui ne sont pas en `https` sur un réseau connu sont écartés. Rien n'est demandé à personne : pas de blocage, pas d'écriture, pas de harcèlement.

## Lancer

```bash
python3 server.py
```

Le site écoute sur le port 8080. Le premier relevé part tout de suite, puis toutes les 60 secondes. La page interroge `/api/status` et recharge `data/fiches.json` quand `as_of` change.

```bash
HOST=0.0.0.0 PORT=8080 REFRESH_SECONDS=60 python3 server.py
```

`REFRESH_SECONDS` ne descend pas sous 15.

Un seul passage, sans serveur :

```bash
python3 refresh.py
```

Les bruts atterrissent dans `data/raw/` (non versionné). Le merge publié est `data/fiches.json`.

Le compteur de passages vit dans `data/visits.json` (non versionné). Il augmente quand quelqu'un ouvre la page, pas quand le navigateur charge une image ou le relevé. `/api/visits` renvoie le total et le jour en cours, en UTC.

## Docker

```bash
docker build -t ondebloquelagauche .
docker run --rm -p 8080:8080 ondebloquelagauche
```

## Adresse publique

`SITE_URL` réécrit le canonical, Open Graph, la carte Twitter et le sitemap. Le serveur n'utilise pas l'en-tête Host. Sans variable valide, ces adresses restent `https://ondebloquelagauche.github.io`. En production, fixe l'adresse réelle, en `http` ou `https`, sans chemin :

```bash
SITE_URL=https://exemple.fr python3 server.py
```

La carte est `media/card.jpg` (1200×630).

Seuls `GET` et `HEAD` passent. Le reste reçoit un 405 et compte dans la même file. Le serveur ne rend pas les chemins en point (dont `.git`), `data/raw/`, `data/visits.json`, ni le code. Le relevé public est `data/fiches.json`. Les liens vers les profils partent sans Referer.

## File d'attente

Pas de répartiteur de charge. Deux requêtes passent en même temps. Douze autres attendent, vingt secondes au plus. Chaque adresse a 16 jetons, qui reviennent au rythme de 0,25 par seconde. Au-delà, la connexion attend, puis reçoit `429` et `Retry-After`.

```bash
MAX_INFLIGHT=2 MAX_QUEUE=12 QUEUE_WAIT=20 IP_CAPACITY=16 IP_REFILL=0.25 IP_WAIT=12 python3 server.py
```

## Ce qui reste affiché si la source refuse

Cloudflare peut renvoyer une page au lieu du JSON. Le serveur garde alors la dernière copie valide et `/api/status` signale un relevé raté. Le détail reste dans la sortie du processus.

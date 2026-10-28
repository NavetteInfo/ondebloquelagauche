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

## Docker

```bash
docker build -t ondebloquelagauche .
docker run --rm -p 8080:8080 ondebloquelagauche
```

## Ce qui reste affiché si la source refuse

Cloudflare peut renvoyer une page au lieu du JSON. Le serveur garde alors la dernière copie valide et `/api/status` porte l'erreur. La page le dit.

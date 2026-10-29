#!/bin/bash
# Empile les JSON déjà déposés dans pulls/deck, puis pousse le site.
set -euo pipefail
cd "$(dirname "$0")"
date -u +"== %Y-%m-%dT%H:%M:%SZ"
# Les retouches faites sur GitHub passent avant le relevé.
git pull -q --rebase --autostash origin main
python3 refresh.py --deck
git add data/merged.json data/fiches.json data/deck data/historique data/comparaison.json
if [ -d data/fcounts ]; then
  git add data/fcounts
fi
if git diff --cached --quiet; then
  exit 0
fi
git commit -m "Assemble les decks."
# Un push refusé est recalé une fois, puis retenté.
git push -q origin main || { git pull -q --rebase --autostash origin main && git push -q origin main; }

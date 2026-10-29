#!/bin/bash
# Empile les JSON déjà déposés dans pulls/deck, puis pousse le site.
set -euo pipefail
cd "$(dirname "$0")"
python3 refresh.py --deck
git add data/merged.json data/fiches.json data/deck data/historique data/comparaison.json
if [ -d data/fcounts ]; then
  git add data/fcounts
fi
if git diff --cached --quiet; then
  exit 0
fi
git commit -m "Assemble les decks."
git push

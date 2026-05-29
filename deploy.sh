#!/bin/bash
# deploy.sh — pubblica su GitHub e aggiorna il container sul VPS.
# Uso: ./deploy.sh
# Ricostruisce l'immagine Docker dal codice corrente — niente docker cp.

set -e

VPS="root@76.13.37.133"
VPS_REPO="/root/fund_monitor_system"
SSH_KEY="$HOME/.ssh/id_ed25519_vps"

# 1. Push su GitHub (solo se ci sono modifiche)
echo "=== [1/3] Push GitHub ==="
git add -A
if git diff --cached --quiet; then
    echo "Nessuna modifica locale — già in sync con GitHub."
else
    git commit -m "Deploy $(date '+%Y-%m-%d %H:%M')"
    git push origin main
fi

# 2. VPS: git pull (scarta modifiche del monitor su Excel, prende ultima versione da GitHub)
echo ""
echo "=== [2/3] Git pull VPS ==="
ssh -i "$SSH_KEY" "$VPS" "
    cd $VPS_REPO
    git fetch origin main
    git reset --hard origin/main
"

# 3. Rebuild immagine Docker e ricrea il container
echo ""
echo "=== [3/3] Build + deploy container ==="
ssh -i "$SSH_KEY" "$VPS" "
    cd $VPS_REPO
    docker compose -p fund-monitor build app
    # Rimuove tutti i container app (anche stale con hash nel nome) prima di ripartire
    docker ps -a --filter name=fund-monitor-app --format '{{.Names}}' | xargs -r docker rm -f
    docker compose -p fund-monitor up -d app
    echo 'Container aggiornato con la nuova immagine.'
"

echo ""
echo "=== [4/4] Trigger monitor (aggiorna dashboard_data.json) ==="
ssh -i "$SSH_KEY" "$VPS" "until curl -sf http://localhost:5000/api/health > /dev/null 2>&1; do sleep 2; done && curl -s -X POST http://localhost:5000/api/trigger-update"
echo ""
echo "Deploy completato. Dashboard: https://fondi.andreapavan.tech"
echo "Il monitor sta girando in background (~10 min). Poi ricarica la dashboard."


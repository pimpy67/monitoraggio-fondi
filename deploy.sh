#!/bin/bash
# deploy.sh — pubblica tutto su GitHub e aggiorna il container sul VPS.
# Uso: ./deploy.sh
# Include anche l'Excel se modificato.

set -e

VPS="root@76.13.37.133"
VPS_REPO="/root/fund_monitor_system"
SSH_KEY="$HOME/.ssh/id_ed25519_vps"
DEPLOY_FILES="dashboard.html monitor.py scheduler.py alerts.py app.py database.py technical_analysis.py data_fetcher.py"

# 1. Push su GitHub (solo se ci sono modifiche)
echo "=== [1/3] Push GitHub ==="
git add $DEPLOY_FILES fondi_monitoraggio.xlsx docker-compose.yml
if git diff --cached --quiet; then
    echo "Nessuna modifica locale — già in sync con GitHub."
else
    git commit -m "Deploy $(date '+%Y-%m-%d %H:%M')"
    git push origin main
fi

# 2. VPS: git pull (scarta modifiche del monitor su Excel/dashboard, prende ultima versione)
echo ""
echo "=== [2/3] Git pull VPS ==="
ssh -i "$SSH_KEY" "$VPS" "
  cd $VPS_REPO
  git checkout -- fondi_monitoraggio.xlsx
  git pull origin main
"

# 3. Copia file nel container e riavvia
echo ""
echo "=== [3/3] Deploy container ==="
ssh -i "$SSH_KEY" "$VPS" "
  cd $VPS_REPO
  for f in $DEPLOY_FILES; do
    docker cp \$f fund-monitor-app-1:/app/\$f
  done
  docker restart fund-monitor-app-1
  echo 'Container aggiornato.'
"

echo ""
echo "Deploy completato. Dashboard: https://fondi.andreapavan.tech"
echo "Per aggiornamento immediato dei prezzi:"
echo "  ssh -i $SSH_KEY $VPS 'curl -s -X POST http://localhost:5000/api/trigger-update'"

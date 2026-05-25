#!/bin/bash
# Pubblica fondi_monitoraggio.xlsx su GitHub e aggiorna il VPS via git pull.
# Esegui questo script ogni volta che modifichi l'Excel (aggiungi/rimuovi fondi, cambia categorie).
# Funziona da qualsiasi PC che abbia git e accesso SSH al VPS.

VPS="root@76.13.37.133"
VPS_REPO="/root/fund_monitor_system"

# 1. Commit e push dell'Excel su GitHub
echo "=== Push Excel su GitHub ==="
git add fondi_monitoraggio.xlsx
if git diff --cached --quiet; then
    echo "Nessuna modifica all'Excel — già aggiornato su GitHub."
else
    git commit -m "Aggiorna fondi_monitoraggio.xlsx"
    git push origin main
    if [ $? -ne 0 ]; then
        echo "ERRORE: push GitHub fallito. Verifica la connessione."
        exit 1
    fi
fi

# 2. VPS: scarta le modifiche del monitor (Livello/Prezzo/RSI) e scarica l'ultimo Excel da GitHub
echo ""
echo "=== Aggiornamento VPS ==="
ssh "$VPS" "cd $VPS_REPO && git checkout -- fondi_monitoraggio.xlsx && git pull origin main"
if [ $? -ne 0 ]; then
    echo "ERRORE: git pull sul VPS fallito. Verifica la connessione SSH."
    exit 1
fi

echo ""
echo "Fatto! Il container legge l'Excel aggiornato al prossimo run del monitor."
echo "Per un aggiornamento immediato:"
echo "  ssh $VPS 'curl -s -X POST http://localhost:5000/api/trigger-update'"

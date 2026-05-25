#!/bin/bash
# Aggiorna l'Excel dei fondi sul server
# Esegui questo script ogni volta che modifichi fondi_monitoraggio.xlsx

EXCEL_MAC="/Users/user/Documents/CORSO_ITS/APPLICAZIONI _ APP/MONITORAGGIO FONDI/fondi_monitoraggio.xlsx"
EXCEL_VPS="root@76.13.37.133:/opt/fund-monitor-src/fondi_monitoraggio.xlsx"

echo "Caricamento Excel sul server..."
scp "$EXCEL_MAC" "$EXCEL_VPS"

if [ $? -eq 0 ]; then
    echo "Fatto! I fondi aggiornati saranno monitorati dal prossimo run del monitor."
    echo "Puoi triggerare un aggiornamento manuale con:"
    echo "  curl -X POST http://localhost:5000/api/trigger-update"
else
    echo "ERRORE: caricamento fallito. Verifica la connessione VPN/SSH."
fi

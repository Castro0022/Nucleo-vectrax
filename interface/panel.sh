#!/bin/bash
clear
echo "═══════════════════════════════════════════════"
echo " 🔮  PANEL VECTRAX – VISUALIZACIÓN DE NÚCLEO "
echo "═══════════════════════════════════════════════"
echo "🧠 Estado del Núcleo:"
curl -s http://127.0.0.1:8080/ | jq '.mensaje, .registros'
echo ""
echo "💾 Últimos eventos de memoria:"
tail -n 5 ~/Vectrax_Nucleo/logs/*.log 2>/dev/null || echo "Sin registros aún"
echo ""
echo "🪶 Firma Activa:"
cat ~/Vectrax_Nucleo/activos/V_Signature.txt
echo ""
echo "═══════════════════════════════════════════════"
echo "Pulso de conciencia: $(date)"
echo "═══════════════════════════════════════════════"

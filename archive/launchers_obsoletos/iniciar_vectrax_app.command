#!/bin/bash
clear
echo "🌕 Iniciando Núcleo Dorado Vectrax..."
sleep 1
cd ~/Vectrax_Nucleo/interface
/opt/homebrew/bin/streamlit run panel_vectrax.py --server.port 8501 --server.headless true


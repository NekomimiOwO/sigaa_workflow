@echo off
:: Define o título da janela do Prompt de Comando
title Automador SIGAA - Streamlit

echo =======================================================
echo   Iniciando a Interface Grafica do Automador SIGAA...
echo =======================================================
echo.

:: Tenta executar o Streamlit usando o modulo do Python
python -m streamlit run app.py

:: Se a linha acima falhar, tenta usando a chamada alternativa 'py'
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Tentando comando alternativo com 'py'...
    py -m streamlit run app.py
)

:: Se ainda assim der erro, mantem a janela aberta para mostrar o motivo
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [X] Erro ao iniciar a aplicacao. Verifique se as dependencias estao instaladas.
    echo.
    pause
)
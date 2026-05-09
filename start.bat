@echo off
setlocal enabledelayedexpansion
title Priya — AI Calling Bot

echo.
echo ============================================================
echo   Priya - AI Calling Bot  ^|  Startup Script
echo ============================================================
echo.

:: ---- Check Python ----
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: ---- Create venv if missing ----
if not exist "venv\Scripts\activate.bat" (
    echo [SETUP] Creating virtual environment...
    python -m venv venv
)

:: ---- Activate venv ----
call venv\Scripts\activate.bat

:: ---- Install / upgrade requirements ----
echo [SETUP] Installing Python dependencies...
pip install -r requirements.txt --quiet --upgrade

:: ---- Check .env ----
if not exist ".env" (
    echo.
    echo [WARNING] .env file not found.
    echo           Copy .env.example to .env and fill in your API keys.
    echo           Then run this script again.
    echo.
    copy .env.example .env >nul
    echo [INFO] Created .env from template. Open it and add your keys.
    pause
    exit /b 1
)

:: ---- Check and start Ollama (optional — only if GROQ_API_KEY is blank) ----
findstr /i "GROQ_API_KEY=" .env | findstr /v "GROQ_API_KEY=$" | findstr /v "GROQ_API_KEY= " >nul 2>&1
if errorlevel 1 (
    echo [INFO] GROQ_API_KEY not set — checking for Ollama...
    ollama --version >nul 2>&1
    if errorlevel 1 (
        echo [INFO] Ollama not installed. Attempting install...
        where winget >nul 2>&1
        if errorlevel 1 (
            echo [WARNING] winget not found. Install Ollama manually from https://ollama.com
        ) else (
            echo [INSTALL] Installing Ollama via winget...
            winget install --id Ollama.Ollama --silent --accept-package-agreements --accept-source-agreements
            if errorlevel 1 (
                echo [WARNING] Ollama install failed. Install from https://ollama.com
            ) else (
                echo [INFO] Ollama installed successfully.
            )
        )
    )
    ollama --version >nul 2>&1
    if errorlevel 1 (
        echo [WARNING] Ollama is still not available. Install it manually from https://ollama.com
        echo           Or set GROQ_API_KEY in .env to use Groq instead.
    ) else (
        echo [OLLAMA] Starting Ollama server...
        start "Ollama" /min ollama serve
        timeout /t 3 /nobreak >nul
        ollama pull llama3.2:3b
        echo [OLLAMA] Model ready.
    )
) else (
    echo [INFO] GROQ_API_KEY found — using Groq for LLM.
)

:: ---- Start FastAPI backend in a new window ----
echo.
echo [BACKEND] Starting FastAPI server on http://localhost:8000 ...
start "Priya Backend" cmd /k "call venv\Scripts\activate.bat && uvicorn main:app --reload --port 8000"

:: ---- Wait for backend to be ready ----
echo [BACKEND] Waiting for server to start...
:wait_loop
timeout /t 2 /nobreak >nul
curl -s http://localhost:8000/health >nul 2>&1
if errorlevel 1 goto wait_loop
echo [BACKEND] Server is up.

:: ---- Open frontend in default browser ----
echo [FRONTEND] Opening landing page...
start "" "%~dp0frontend\index.html"

echo.
echo ============================================================
echo   Everything is running.
echo.
echo   Backend:  http://localhost:8000
echo   Frontend: frontend/index.html (opened in browser)
echo   Health:   http://localhost:8000/health
echo.
echo   To stop: close the "Priya Backend" terminal window.
echo ============================================================
echo.
pause

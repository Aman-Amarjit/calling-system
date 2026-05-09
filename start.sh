#!/usr/bin/env bash
set -e

echo ""
echo "============================================================"
echo "  Priya - AI Calling Bot  |  Startup Script"
echo "============================================================"
echo ""

# ---- Check Python ----
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 not found. Install Python 3.10+ first."
    exit 1
fi

# ---- Create venv if missing ----
if [ ! -f "venv/bin/activate" ]; then
    echo "[SETUP] Creating virtual environment..."
    python3 -m venv venv
fi

# ---- Activate venv ----
source venv/bin/activate

# ---- Install / upgrade requirements ----
echo "[SETUP] Installing Python dependencies..."
pip install -r requirements.txt --quiet --upgrade

# ---- Check .env ----
if [ ! -f ".env" ]; then
    echo ""
    echo "[WARNING] .env file not found."
    cp .env.example .env
    echo "[INFO] Created .env from template. Open it, fill in your API keys, then run again."
    echo ""
    exit 1
fi

# ---- Ollama (optional — used when GROQ_API_KEY is blank) ----
GROQ_KEY=$(grep -E "^GROQ_API_KEY=.+" .env | cut -d= -f2 | tr -d ' ')
if [ -z "$GROQ_KEY" ]; then
    echo "[INFO] GROQ_API_KEY not set — checking for Ollama..."
    if command -v ollama &>/dev/null; then
        echo "[OLLAMA] Starting Ollama server..."
        ollama serve &>/dev/null &
        sleep 2
        echo "[OLLAMA] Pulling llama3.2:3b (first run only, ~2GB)..."
        ollama pull llama3.2:3b
        echo "[OLLAMA] Model ready."
    else
        echo "[WARNING] Ollama not found. Install from https://ollama.com"
        echo "          Or set GROQ_API_KEY in .env to use Groq instead."
    fi
else
    echo "[INFO] GROQ_API_KEY found — using Groq for LLM."
fi

# ---- Start FastAPI backend ----
echo ""
echo "[BACKEND] Starting FastAPI server on http://localhost:8000 ..."
uvicorn main:app --reload --port 8000 &
BACKEND_PID=$!

# ---- Wait for backend ----
echo "[BACKEND] Waiting for server to start..."
for i in {1..15}; do
    sleep 1
    if curl -s http://localhost:8000/health &>/dev/null; then
        echo "[BACKEND] Server is up."
        break
    fi
done

# ---- Open frontend ----
echo "[FRONTEND] Opening landing page..."
FRONTEND_PATH="$(pwd)/frontend/index.html"

if command -v xdg-open &>/dev/null; then
    xdg-open "$FRONTEND_PATH"
elif command -v open &>/dev/null; then
    open "$FRONTEND_PATH"
else
    echo "[FRONTEND] Open this file in your browser: $FRONTEND_PATH"
fi

echo ""
echo "============================================================"
echo "  Everything is running."
echo ""
echo "  Backend:  http://localhost:8000"
echo "  Frontend: frontend/index.html"
echo "  Health:   http://localhost:8000/health"
echo ""
echo "  Press Ctrl+C to stop the backend."
echo "============================================================"
echo ""

# Keep script alive so Ctrl+C stops the backend
wait $BACKEND_PID

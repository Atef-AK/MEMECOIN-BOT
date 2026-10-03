# start_backend.ps1 - Start the FastAPI backend
Write-Host "Starting Solana Memecoin Bot Backend..." -ForegroundColor Cyan

# Activate venv
if (Test-Path "venv\Scripts\Activate.ps1") {
    & .\venv\Scripts\Activate.ps1
}

# Start backend
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

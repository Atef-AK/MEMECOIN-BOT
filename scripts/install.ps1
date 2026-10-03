# install.ps1 - Install dependencies for local development
Write-Host "====================================" -ForegroundColor Cyan
Write-Host "Solana Memecoin Bot - Installation" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan

# Check Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "ERROR: Python 3.12+ is required but not found in PATH" -ForegroundColor Red
    Write-Host "Install from https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}

$version = python --version
Write-Host "Found: $version" -ForegroundColor Green

# Check Node.js
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Write-Host "WARNING: Node.js not found. Frontend will not be available." -ForegroundColor Yellow
    Write-Host "Install from https://nodejs.org/" -ForegroundColor Yellow
}

# Create virtual environment
Write-Host "`nCreating virtual environment..." -ForegroundColor Cyan
if (-not (Test-Path "venv")) {
    python -m venv venv
}
Write-Host "Virtual environment ready" -ForegroundColor Green

# Activate and install backend
Write-Host "`nInstalling backend dependencies..." -ForegroundColor Cyan
& .\venv\Scripts\Activate.ps1
pip install -e ".[dev]" 2>&1 | Out-Null
Write-Host "Backend dependencies installed" -ForegroundColor Green

# Install frontend
if ($node) {
    Write-Host "`nInstalling frontend dependencies..." -ForegroundColor Cyan
    Set-Location frontend
    npm install 2>&1 | Out-Null
    Set-Location ..
    Write-Host "Frontend dependencies installed" -ForegroundColor Green
}

# Create .env if not exists
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "`nCreated .env from .env.example" -ForegroundColor Yellow
    Write-Host "IMPORTANT: Edit .env with your RPC URL and API keys" -ForegroundColor Yellow
}

# Create logs directory
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

Write-Host "`n====================================" -ForegroundColor Green
Write-Host "Installation complete!" -ForegroundColor Green
Write-Host "====================================" -ForegroundColor Green
Write-Host "`nNext steps:" -ForegroundColor Cyan
Write-Host "1. Edit .env with your configuration"
Write-Host "2. Run: .\scripts\start_bot.ps1"
Write-Host "3. Run: .\scripts\start_frontend.ps1 (in another terminal)"
Write-Host "4. Open: http://localhost:5173"

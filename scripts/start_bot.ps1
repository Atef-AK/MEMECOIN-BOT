# start_bot.ps1 - Start the complete bot (backend + frontend)
Write-Host "====================================" -ForegroundColor Cyan
Write-Host "SOLANA MEMECOIN BOT" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan

# Check .env
if (-not (Test-Path ".env")) {
    Write-Host "ERROR: .env file not found. Run install.ps1 first." -ForegroundColor Red
    exit 1
}

# Start backend in background
Write-Host "`nStarting backend..." -ForegroundColor Green
$backend = Start-Process -FilePath "powershell" -ArgumentList "-File", ".\scripts\start_backend.ps1" -PassThru -WindowStyle Normal
Write-Host "Backend PID: $($backend.Id)" -ForegroundColor Gray

# Wait for backend to be ready
Start-Sleep -Seconds 3

# Start frontend
Write-Host "Starting frontend..." -ForegroundColor Green
$frontend = Start-Process -FilePath "powershell" -ArgumentList "-File", ".\scripts\start_frontend.ps1" -PassThru -WindowStyle Normal
Write-Host "Frontend PID: $($frontend.Id)" -ForegroundColor Gray

Write-Host "`n====================================" -ForegroundColor Green
Write-Host "Bot is running!" -ForegroundColor Green
Write-Host "Backend:  http://localhost:8000" -ForegroundColor Cyan
Write-Host "Frontend: http://localhost:5173" -ForegroundColor Cyan
Write-Host "API Docs: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Green
Write-Host "`nPress Ctrl+C to stop" -ForegroundColor Yellow

# Wait for user to stop
try {
    Wait-Process -Id $backend.Id
} catch {
    Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue
    Stop-Process -Id $frontend.Id -ErrorAction SilentlyContinue
}

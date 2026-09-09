Set-Location $PSScriptRoot
$py = "py"
try { & $py --version | Out-Null } catch { $py = "python" }
& $py --version
if ($LASTEXITCODE -ne 0) {
  Write-Host "Python 3.10+ is required."
  Read-Host "Press Enter to exit"
  exit 1
}
& $py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
  Write-Host "Package installation failed."
  Read-Host "Press Enter to exit"
  exit 1
}
Start-Process "http://127.0.0.1:8000/"
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000

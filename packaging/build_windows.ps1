# Baut die Windows-Anwendung (Ordner dist\KI-Ordnerorganisator) und die Kommandozeilen-Version.
# Aufruf in PowerShell im Projektordner:  .\packaging\build_windows.ps1

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
Set-Location (Join-Path $PSScriptRoot "..")

python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

# Grafische Anwendung (ohne Konsolenfenster)
python -m PyInstaller --noconfirm --clean --windowed `
    --name "KI-Ordnerorganisator" `
    --collect-data ordnerorganisator `
    --hidden-import keyring.backends.Windows `
    packaging\launcher_gui.py

# Kommandozeile als einzelne Datei (ohne Qt, daher klein)
python -m PyInstaller --noconfirm --clean --onefile --console `
    --name "ki-ordnerorganisator-cli" `
    --collect-data ordnerorganisator `
    --hidden-import keyring.backends.Windows `
    --exclude-module PySide6 `
    packaging\launcher_cli.py

Copy-Item "dist\ki-ordnerorganisator-cli.exe" "dist\KI-Ordnerorganisator\" -Force
Write-Host ""
Write-Host "Fertig: dist\KI-Ordnerorganisator\KI-Ordnerorganisator.exe"

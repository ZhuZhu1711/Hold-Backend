$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if ($env:GITHUB_ACTIONS -eq 'true' -or $env:CI -eq 'true') {
    $cmd = Get-Command python -ErrorAction Stop
    $py = $cmd.Source
} else {
    $py = Join-Path $env:USERPROFILE '.conda\envs\web\python.exe'
    if (-not (Test-Path $py)) {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $cmd) {
            Write-Error '找不到 Python。请先 conda activate web，或把 web 环境的 python.exe 加入 PATH。'
        }
        $py = $cmd.Source
    }
}

Write-Host "Using: $py"
& $py -c "import PyInstaller, waitress; print('PyInstaller OK')"
if ($LASTEXITCODE -ne 0) {
    Write-Error '当前 Python 缺少 PyInstaller 或 waitress，请先: pip install -r requirements.txt'
}

& $py -m PyInstaller --noconfirm --clean main.spec
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$exe = Join-Path $PSScriptRoot 'dist\HoldBackend.exe'
Write-Host ""
Write-Host "Build OK: $exe"
Write-Host "启动: .\dist\HoldBackend.exe"
Write-Host "调试模式: .\dist\HoldBackend.exe --mode debug"

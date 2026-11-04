$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

if ($env:GITHUB_ACTIONS -eq 'true' -or $env:GITLAB_CI -eq 'true' -or $env:CI -eq 'true') {
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

& $py -c "from app.config import Config; print('HOLD_PREDICT_ENABLED=', Config.HOLD_PREDICT_ENABLED)"
if ($LASTEXITCODE -ne 0) {
    Write-Error '无法读取 app.config.HOLD_PREDICT_ENABLED'
}

& $py -m PyInstaller --noconfirm --clean main.spec
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

# 第二次不要 --clean，避免清掉刚打好的 HoldBackend.exe
& $py -m PyInstaller --noconfirm FT_merge.spec
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

# 第三次同样不要 --clean，避免清掉前两个 exe
& $py -m PyInstaller --noconfirm CP_merge.spec
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$backend = Join-Path $PSScriptRoot 'dist\HoldBackend.exe'
$merge = Join-Path $PSScriptRoot 'dist\FT_HoldMerge.exe'
$cpMerge = Join-Path $PSScriptRoot 'dist\CP_HoldMerge.exe'
Write-Host ""
Write-Host "Build OK: $backend"
Write-Host "Build OK: $merge"
Write-Host "Build OK: $cpMerge"
Write-Host "后台: .\dist\HoldBackend.exe"
Write-Host "FT合批: .\dist\FT_HoldMerge.exe"
Write-Host "CP合批: .\dist\CP_HoldMerge.exe"
Write-Host "不设置 HOLD_ENV 为测试；正式机必须设 HOLD_ENV=release"

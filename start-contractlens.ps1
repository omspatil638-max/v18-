<#
    ContractLens launcher (Windows PowerShell 5.1 compatible).

    Fixed in this version:
      * No dependency on psql / pg_isready. The database is checked and created with
        the backend's own asyncpg (backend/scripts/ensure_db.py), so a machine that
        runs PostgreSQL only in Docker no longer fails with "pg_isready not found".
      * If Docker Desktop is installed but not running, it is started and waited for,
        instead of falling back to a local PostgreSQL that may not exist.
      * The browser opens only once the app actually answers. Next.js needs ~20s for
        its first compile; the old fixed 3-second wait showed "can't reach this page".
      * Servers already listening are detected and reused instead of failing to bind.
      * Default mode is "auto".
#>
param(
    [ValidateSet("local", "docker", "auto")]
    [string]$DatabaseMode = "auto",
    [switch]$NoBrowser,
    [int]$ReadyTimeoutSeconds = 240
)

$ErrorActionPreference = "Stop"

$Root          = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend       = Join-Path $Root "backend"
$Frontend      = Join-Path $Root "frontend"
$BackendPython = Join-Path $Backend ".venv\Scripts\python.exe"
$BackendEnv    = Join-Path $Backend ".env"
$DepStamp      = Join-Path $Backend ".venv\.requirements.sha1"
$BackendPort   = 8001
$FrontendPort  = 3000

function Write-Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Ok  ([string]$Text) { Write-Host "    $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "    $Text" -ForegroundColor DarkGray }
function Write-Warn([string]$Text) { Write-Host "    $Text" -ForegroundColor Yellow }

function Fail([string]$Message, [string[]]$Hints) {
    Write-Host "`nERROR: $Message" -ForegroundColor Red
    foreach ($h in $Hints) { Write-Host "  - $h" -ForegroundColor Yellow }
    exit 1
}

function Require-Command([string]$Name, [string]$InstallHint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Fail "$Name was not found on PATH." @($InstallHint)
    }
}

# Windows PowerShell 5.1 turns ANY stderr output from a native command into a terminating
# error while $ErrorActionPreference is "Stop" - even with *> $null. Docker writes to stderr
# both when the engine is down and for normal progress lines ("Container x Started"), so every
# docker call goes through this wrapper, which judges success by exit code only.
function Invoke-Docker([string[]]$DockerArgs, [switch]$Quiet) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($Quiet) { $null = & docker @DockerArgs 2>&1 }
        else        { & docker @DockerArgs 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
        return $LASTEXITCODE
    } catch {
        return 1
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Test-DockerReady { return ((Invoke-Docker @("info") -Quiet) -eq 0) }

function Test-PortInUse([int]$Port) {
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return ($null -ne $c)
}

function Test-Url([string]$Url, [int]$TimeoutSec = 3) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Wait-Url([string]$Url, [int]$TimeoutSec, [string]$Label) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $tick = 0
    while ((Get-Date) -lt $deadline) {
        if (Test-Url $Url 3) { return $true }
        $tick++
        if ($tick % 5 -eq 0) {
            $left = [int](($deadline - (Get-Date)).TotalSeconds)
            Write-Note "still waiting for $Label ... ${left}s left"
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

Write-Host "ContractLens launcher" -ForegroundColor White
Write-Note "repository: $Root"

# ── 1. Prerequisites ─────────────────────────────────────────────────────────
Write-Step "Checking prerequisites"
Require-Command "python" "Install Python 3.10 or newer from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."
Require-Command "npm"    "Install Node.js 18 or newer from https://nodejs.org/."
Write-Ok "python and npm found"

# ── 2. backend/.env ──────────────────────────────────────────────────────────
if (-not (Test-Path $BackendEnv)) {
    Write-Step "Creating backend\.env"
    @"
# ─── LLM ─────────────────────────────────────────────────────────
# AI is OFF by default. For free AI extraction create a key at
# https://console.groq.com/keys then set LLM_PROVIDER=groq and paste
# the key into GROQ_API_KEY. Put the key ONLY in GROQ_API_KEY.
LLM_PROVIDER=none
LLM_MODEL=
GROQ_API_KEY=
ANTHROPIC_API_KEY=
LLM_BASE_URL=
OPENAI_API_KEY=

DATABASE_URL=postgresql+asyncpg://contractlens:contractlens@localhost:5432/contractlens
STORAGE_PATH=./storage
MAX_UPLOAD_MB=20

AUTH_MODE=demo
CORS_ORIGINS=http://localhost:3000
APP_ENV=development
LOG_LEVEL=info
"@ | Set-Content -Path $BackendEnv -Encoding UTF8
    Write-Ok "created (AI extraction stays off until you add a key - see README)"
}

# ── 3. Python virtual environment + dependencies ─────────────────────────────
if (-not (Test-Path $BackendPython)) {
    Write-Step "Creating the Python virtual environment"
    & python -m venv (Join-Path $Backend ".venv")
    if (-not (Test-Path $BackendPython)) {
        Fail "The virtual environment could not be created." @("Run manually: python -m venv backend\.venv")
    }
}

$reqPath  = Join-Path $Backend "requirements.txt"
$reqHash  = (Get-FileHash -Path $reqPath -Algorithm SHA1).Hash
$haveHash = ""
if (Test-Path $DepStamp) { $haveHash = (Get-Content $DepStamp -Raw).Trim() }

if ($haveHash -ne $reqHash) {
    Write-Step "Installing backend dependencies (first run, or requirements changed)"
    & $BackendPython -m pip install --disable-pip-version-check -q -r $reqPath
    if ($LASTEXITCODE -ne 0) {
        Fail "Backend dependencies could not be installed." @("Check your internet connection, then run this launcher again.")
    }
    Set-Content -Path $DepStamp -Value $reqHash -Encoding ASCII
    Write-Ok "dependencies installed"
} else {
    Write-Note "backend dependencies already up to date"
}

# ── 4. Database ──────────────────────────────────────────────────────────────
Write-Step "Preparing PostgreSQL"

$dbListening = Test-PortInUse 5432
$usedDocker  = $false

if ($DatabaseMode -eq "local") {
    if (-not $dbListening) {
        $svc = Get-Service | Where-Object { $_.Name -match "^postgresql" } | Select-Object -First 1
        if ($svc -and $svc.Status -ne "Running") {
            Write-Note "starting the $($svc.Name) Windows service..."
            Start-Service -Name $svc.Name
            Start-Sleep -Seconds 3
        }
    }
    Write-Note "using local PostgreSQL on port 5432"
} elseif ($dbListening -and $DatabaseMode -eq "auto") {
    Write-Ok "a PostgreSQL server is already listening on port 5432 - reusing it"
} else {
    $dockerCmd = Get-Command "docker" -ErrorAction SilentlyContinue
    if (-not $dockerCmd) {
        if ($DatabaseMode -eq "docker") {
            Fail "Docker mode was requested but Docker is not installed." @(
                "Install Docker Desktop, or install PostgreSQL and run: .\start-contractlens.ps1 -DatabaseMode local"
            )
        }
        Fail "Nothing is listening on port 5432 and Docker is not installed." @(
            "Easiest: install Docker Desktop (https://www.docker.com/products/docker-desktop/), start it, then run this launcher again.",
            "Or: install PostgreSQL 16, create user and database 'contractlens', then run: .\start-contractlens.ps1 -DatabaseMode local"
        )
    }

    $dockerReady = Test-DockerReady

    if (-not $dockerReady) {
        Write-Note "Docker Desktop is not running - starting it (this can take a minute)..."
        $dd = @(
            "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
            "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($dd) { Start-Process -FilePath $dd | Out-Null }

        $deadline = (Get-Date).AddSeconds(150)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 5
            if (Test-DockerReady) { $dockerReady = $true; break }
            Write-Note "waiting for the Docker engine..."
        }
    }

    if (-not $dockerReady) {
        Fail "Docker Desktop did not become ready." @(
            "Open Docker Desktop manually, wait until it reports 'Engine running', then run this launcher again.",
            "Or install PostgreSQL locally and use: .\start-contractlens.ps1 -DatabaseMode local"
        )
    }

    Write-Note "starting the PostgreSQL container..."
    $composeExit = Invoke-Docker @("compose", "--project-name", "contractlens", "-f", (Join-Path $Root "docker-compose.yml"), "up", "-d")
    if ($composeExit -ne 0) {
        Fail "The PostgreSQL container could not be started." @("Check the logs: docker compose --project-name contractlens logs")
    }
    $usedDocker = $true
    Write-Ok "container started"
}

# Works in every mode: waits for the server and creates the database if it is missing.
# Uses asyncpg from the backend venv, so psql / pg_isready are not required.
Push-Location $Backend
try {
    & $BackendPython -m scripts.ensure_db --wait 90 --with-test-db
    $dbExit = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($dbExit -ne 0) {
    $hints = @("Check DATABASE_URL in backend\.env.")
    if ($usedDocker) { $hints += "Check the container logs: docker compose --project-name contractlens logs" }
    else { $hints += "Make sure PostgreSQL is running and accepts the user and password in DATABASE_URL." }
    Fail "The database is not reachable." $hints
}

# ── 5. Migrations ────────────────────────────────────────────────────────────
Write-Step "Applying database migrations"
Push-Location $Backend
try {
    & $BackendPython -m alembic upgrade head
    $migExit = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($migExit -ne 0) {
    Fail "Database migrations failed." @("Run them manually: cd backend; .\.venv\Scripts\python -m alembic upgrade head")
}
Write-Ok "schema up to date"

# Browser-notification keys: created once, kept in backend\.env (never printed). Harmless if already present.
Push-Location $Backend
try {
    & $BackendPython -m scripts.setup_notifications | Out-Null
} finally {
    Pop-Location
}

# ── 6. Frontend dependencies ─────────────────────────────────────────────────
if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
    Write-Step "Installing frontend dependencies (a few minutes the first time)"
    Push-Location $Frontend
    try {
        & npm install
        $npmExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($npmExit -ne 0) { Fail "Frontend dependencies could not be installed." @("Run manually: cd frontend; npm install") }
    Write-Ok "installed"
}

# ── 7. Start the servers (reusing anything already healthy) ──────────────────
Write-Step "Starting ContractLens"

if (Test-Url "http://127.0.0.1:$BackendPort/health" 3) {
    Write-Note "backend already running on port $BackendPort - reusing it"
} elseif (Test-PortInUse $BackendPort) {
    Fail "Port $BackendPort is in use by another program." @(
        "Identify it: Get-NetTCPConnection -LocalPort $BackendPort -State Listen | Select-Object OwningProcess",
        "Close that program (or the other ContractLens copy) and run this launcher again."
    )
} else {
    Start-Process powershell.exe -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass",
        "-Command", "`$Host.UI.RawUI.WindowTitle='ContractLens backend'; Set-Location '$Backend'; & '$BackendPython' -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort"
    ) | Out-Null
    Write-Note "backend starting in its own window..."
}

if (Test-Url "http://localhost:$FrontendPort" 3) {
    Write-Note "frontend already running on port $FrontendPort - reusing it"
} elseif (Test-PortInUse $FrontendPort) {
    Fail "Port $FrontendPort is in use by another program." @(
        "Identify it: Get-NetTCPConnection -LocalPort $FrontendPort -State Listen | Select-Object OwningProcess",
        "Close that program (or the other ContractLens copy) and run this launcher again."
    )
} else {
    Start-Process powershell.exe -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass",
        "-Command", "`$Host.UI.RawUI.WindowTitle='ContractLens frontend'; Set-Location '$Frontend'; & npm run dev"
    ) | Out-Null
    Write-Note "frontend starting in its own window..."
}

# ── 8. Wait until the app really answers, THEN open the browser ──────────────
Write-Step "Waiting for the app to be ready"
if (-not (Wait-Url "http://127.0.0.1:$BackendPort/health" 90 "the backend")) {
    Fail "The backend did not start within 90 seconds." @(
        "Look at the 'ContractLens backend' window for the error.",
        "Most common cause: the database is unreachable, or a value in backend\.env is invalid."
    )
}
Write-Ok "backend ready at http://127.0.0.1:$BackendPort"

Write-Note "compiling the frontend (Next.js takes about 20 seconds from cold)..."
if (-not (Wait-Url "http://localhost:$FrontendPort" $ReadyTimeoutSeconds "the frontend")) {
    Fail "The frontend did not start within $ReadyTimeoutSeconds seconds." @(
        "Look at the 'ContractLens frontend' window for the error.",
        "If it is still compiling, open http://localhost:$FrontendPort manually in a moment."
    )
}
Write-Ok "frontend ready at http://localhost:$FrontendPort"

# ── 9. Report what the app can actually do ───────────────────────────────────
try {
    $status = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/system/status" -TimeoutSec 5
    if ($status.llm_configured) {
        Write-Ok "AI extraction: ON ($($status.llm_provider) / $($status.llm_model))"
    } else {
        Write-Warn "AI extraction: OFF - uploads and search work, but extracted fields show 'Extraction unavailable'."
        Write-Warn "To enable it: create a free key at https://console.groq.com/keys, then in backend\.env set"
        Write-Warn "LLM_PROVIDER=groq and paste the key into GROQ_API_KEY (the key goes in GROQ_API_KEY only), and restart."
    }
} catch {
    Write-Note "(could not read /api/system/status)"
}

if (-not $NoBrowser) { Start-Process "http://localhost:$FrontendPort" | Out-Null }

Write-Host "`nContractLens is running:" -ForegroundColor Green
Write-Host "  App       http://localhost:$FrontendPort"
Write-Host "  API docs  http://127.0.0.1:$BackendPort/docs"
Write-Host "`nTo stop it, close the two windows titled 'ContractLens backend' and 'ContractLens frontend'.`n"

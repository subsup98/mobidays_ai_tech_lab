param(
    [string]$InputPath = "",
    [ValidateSet("audio", "transcript")]
    [string]$InputMode = "transcript",
    [string]$TranscriptInput = "data/sample_transcript.json",
    [string]$AudioInput = "data/raw/ko_meeting_3speakers_4min_faster.mp3",
    [string]$TranscriptOutput = "data/processed/app_audio_transcript.json",
    [string]$DatabaseUrl = "",
    [string]$DatabasePath = "data/app_quality.db",
    [int]$NumSpeakers = 0,
    [switch]$UseMock,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvStreamlit = Join-Path $ProjectRoot ".venv\Scripts\streamlit.exe"

function Ensure-Venv {
    if (Test-Path $VenvPython) {
        return
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python 3.10+ is required. Install Python and run this script again."
    }

    Write-Host "Creating virtual environment: .venv"
    & $python.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Virtual environment creation failed."
    }
}

function Ensure-Requirements {
    param(
        [string]$RequirementsPath,
        [string]$StampName
    )

    if ($SkipInstall) {
        return
    }
    if (-not (Test-Path $RequirementsPath)) {
        throw "Requirements file not found: $RequirementsPath"
    }

    $stampPath = Join-Path $ProjectRoot ".venv\$StampName"
    $hash = (Get-FileHash $RequirementsPath -Algorithm SHA256).Hash
    if ((Test-Path $stampPath) -and ((Get-Content $stampPath -Raw).Trim() -eq $hash)) {
        return
    }

    Write-Host "Installing dependencies from $RequirementsPath"
    & $VenvPython -m pip install -r $RequirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed: $RequirementsPath"
    }

    Set-Content -Path $stampPath -Value $hash
}

function Ensure-DotEnv {
    if ((Test-Path ".env") -or -not (Test-Path ".env.example")) {
        return
    }

    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

function Import-DotEnv {
    param([string]$Path = ".env")

    if (-not (Test-Path $Path)) {
        return
    }

    Get-Content -Path $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line.Split("=", 2)
            $name = $parts[0].Trim()
            $value = $parts[1].Trim().Trim('"').Trim("'")

            if ($name -and -not [Environment]::GetEnvironmentVariable($name, "Process")) {
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
    }
}

function Has-Value {
    param([string]$Value)
    return -not [string]::IsNullOrWhiteSpace($Value)
}

Ensure-Venv
Ensure-Requirements -RequirementsPath "requirements.txt" -StampName ".requirements.txt.sha256"
if ($InputMode -eq "audio") {
    Ensure-Requirements -RequirementsPath "requirements-stt.txt" -StampName ".requirements-stt.txt.sha256"
}
Ensure-DotEnv
Import-DotEnv

if (-not (Has-Value $DatabaseUrl)) {
    $DatabaseUrl = $env:DATABASE_URL
}
if (-not (Has-Value $DatabaseUrl)) {
    $DatabaseUrl = "postgresql://postgres:postgres@localhost:5432/mobidays_app"
}

if ($UseMock) {
    $env:LLM_PROVIDER = "mock"
    $env:LLM_FALLBACK = "mock"
    Write-Host "Using mock extractor by explicit -UseMock option."
} else {
    $env:LLM_PROVIDER = "gemini"
    $env:LLM_FALLBACK = "mock"
    Write-Host "Using Gemini extractor."
}

if (-not (Has-Value $InputPath)) {
    if ($InputMode -eq "audio") {
        $InputPath = $AudioInput
    } else {
        $InputPath = $TranscriptInput
    }
}

if (-not (Test-Path $InputPath)) {
    throw "Input not found: $InputPath"
}

$env:DATABASE_URL = $DatabaseUrl
$env:DATABASE_PATH = $DatabasePath
$env:VECTOR_DB_PATH = if (Has-Value $env:VECTOR_DB_PATH) { $env:VECTOR_DB_PATH } else { "data/vector/faiss_action_items" }
$env:DIARIZATION_REQUIRE_SUCCESS = "0"

$dbBackend = "postgres"
$env:DB_BACKEND = $dbBackend

Write-Host "Checking PostgreSQL connection: $DatabaseUrl"
try {
    & $VenvPython -c "import os; from db.pg_client import PostgreSQLClient; c=PostgreSQLClient(os.environ['DATABASE_URL']); c.init_schema(); print('postgres ok')"
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL connection check failed."
    }
} catch {
    $dbBackend = "sqlite"
    $env:DB_BACKEND = $dbBackend
}

$pipelineArgs = @(
    "pipeline.py",
    "--input", $InputPath,
    "--input-mode", $InputMode,
    "--db-backend", $dbBackend,
    "--transcript-output", $TranscriptOutput
)

if ($dbBackend -eq "postgres") {
    $pipelineArgs += @("--pg-dsn", $DatabaseUrl)
} else {
    $pipelineArgs += @("--db", $DatabasePath)
}

if ($NumSpeakers -gt 0) {
    $pipelineArgs += @("--num-speakers", $NumSpeakers)
}

Write-Host "Running pipeline with DB=$dbBackend, input_mode=$InputMode, LLM_PROVIDER=$env:LLM_PROVIDER."
& $VenvPython @pipelineArgs
if ($LASTEXITCODE -ne 0) {
    throw "Pipeline failed."
}

$maintenanceCode = "import os; from pipeline import build_db_client; from analytics.keywords import regenerate_issue_keywords; from integrations.slack_mock import generate_and_store_payloads; c=build_db_client(db_backend=os.getenv('DB_BACKEND','postgres'), db_path=os.getenv('DATABASE_PATH','data/app_quality.db'), pg_dsn=os.getenv('DATABASE_URL')); c.init_schema(); mids=[r['meeting_id'] for r in c.fetch_all('SELECT meeting_id FROM meetings')]; [regenerate_issue_keywords(c,m) for m in mids]; [generate_and_store_payloads(c,m) for m in mids]; print({'db_backend': os.getenv('DB_BACKEND'), 'meetings': len(mids)})"
& $VenvPython -c $maintenanceCode
if ($LASTEXITCODE -ne 0) {
    throw "Post-processing failed."
}

Write-Host "Starting dashboard with DB_BACKEND=$dbBackend"
& $VenvStreamlit run dashboard/app.py
if ($LASTEXITCODE -ne 0) {
    throw "Dashboard failed."
}

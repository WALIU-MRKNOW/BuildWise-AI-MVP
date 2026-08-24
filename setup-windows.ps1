$ErrorActionPreference = "Stop"

$buildwiseRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$privateEnvPath = Join-Path $buildwiseRoot ".env"

function Read-PrivateValue {
    param([Parameter(Mandatory = $true)][string]$Prompt)

    $secureValue = Read-Host -Prompt $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

Clear-Host
Write-Host "BuildWise AI - Private Setup" -ForegroundColor Cyan
Write-Host "Your secret values stay on this laptop and will not be displayed." -ForegroundColor Gray
Write-Host

$telegramToken = Read-PrivateValue "Paste the NEW Telegram bot token"
if ($telegramToken -notmatch "^[0-9]{8,12}:[A-Za-z0-9_-]{30,}$") {
    throw "That does not look like a Telegram bot token. Nothing was saved."
}

$openAiKey = Read-PrivateValue "Paste the NEW OpenAI API key"
if ($openAiKey -notmatch "^sk-") {
    throw "That does not look like an OpenAI API key. Nothing was saved."
}

$supabaseUrl = (Read-Host "Paste the Supabase project URL").Trim().TrimEnd("/")
if ($supabaseUrl -notmatch "^https://[A-Za-z0-9-]+\.supabase\.co$") {
    throw "That does not look like a Supabase project URL. Nothing was saved."
}

$supabaseSecret = Read-PrivateValue "Paste a NEW Supabase sb_secret_ key"
if ($supabaseSecret -notmatch "^sb_secret_") {
    throw "Use a new key beginning with sb_secret_. Nothing was saved."
}

$settingsLines = @(
    "# Private local settings. This file is ignored by Git.",
    "TELEGRAM_BOT_TOKEN=$telegramToken",
    "OPENAI_API_KEY=$openAiKey",
    "SUPABASE_URL=$supabaseUrl",
    "SUPABASE_SECRET_KEY=$supabaseSecret",
    "",
    "OPENAI_MODEL=gpt-5.6-terra",
    "OPENAI_MAX_OUTPUT_TOKENS=1200",
    "OPENAI_IMAGE_DETAIL=auto",
    "OPENAI_TIMEOUT_SECONDS=90",
    "",
    "SUPABASE_STORAGE_BUCKET=buildwise-uploads",
    "MAX_FILE_SIZE_MB=10",
    "",
    "HISTORY_TURNS=8",
    "DAILY_AI_REQUEST_LIMIT=20",
    "REQUEST_COOLDOWN_SECONDS=2",
    "REMINDER_POLL_SECONDS=60",
    "",
    "ALLOWED_USER_IDS=",
    "HOST=0.0.0.0",
    "PORT=8000",
    "LOG_LEVEL=INFO"
)

[IO.File]::WriteAllLines($privateEnvPath, $settingsLines, [Text.UTF8Encoding]::new($false))

$telegramToken = $null
$openAiKey = $null
$supabaseSecret = $null
$settingsLines = $null

Write-Host
Write-Host "Private settings saved successfully." -ForegroundColor Green
Write-Host "Return to Codex and say: settings saved" -ForegroundColor Cyan
Read-Host "Press Enter to close"

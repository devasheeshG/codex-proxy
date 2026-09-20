$ErrorActionPreference = 'Stop'

$ProxyBaseUrl = $CODEX_PROXY_URL
if (-not $ProxyBaseUrl) {
    Write-Host 'Usage: powershell -NoProfile -Command "Set-Variable CODEX_PROXY_URL ''<API_BASE_URL>''; irm ''<PROXY_ORIGIN>/install.ps1'' | iex"'
    Write-Host 'The installer prompts for the API key without echoing it.'
    Write-Host 'For automation, set CODEX_PROXY_KEY_FILE to a protected file containing the key.'
    exit 1
}
if ($ProxyBaseUrl -notmatch '^https?://') {
    throw 'API base URL must start with http:// or https://'
}
if ($ProxyBaseUrl -match '["\\\r\n]') {
    throw 'API base URL contains unsupported characters'
}

$ProxyUserKey = $null
if ($env:CODEX_PROXY_KEY_FILE) {
    if (-not (Test-Path -LiteralPath $env:CODEX_PROXY_KEY_FILE -PathType Leaf)) {
        throw 'CODEX_PROXY_KEY_FILE must name a readable file.'
    }
    $ProxyUserKey = (Get-Content -Raw -LiteralPath $env:CODEX_PROXY_KEY_FILE).Trim()
} else {
    $SecureProxyUserKey = Read-Host 'Paste the Codex Proxy API key (input hidden)' -AsSecureString
    $KeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureProxyUserKey)
    try {
        $ProxyUserKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($KeyPointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($KeyPointer)
        $SecureProxyUserKey.Dispose()
    }
}
if ([string]::IsNullOrWhiteSpace($ProxyUserKey)) {
    throw 'API key cannot be empty.'
}

$CodexDir = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$BinDir = Join-Path $CodexDir 'bin'
$ConfigFile = Join-Path $CodexDir 'config.toml'
$KeyFile = Join-Path $CodexDir 'codex-proxy.key'
$TokenHelper = Join-Path $BinDir 'codex-proxy-token.ps1'
$ModelBaseUrl = "$($ProxyBaseUrl.TrimEnd('/'))/v1"

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
[IO.File]::WriteAllText($KeyFile, $ProxyUserKey, [Text.UTF8Encoding]::new($false))
$ProxyUserKey = $null

@"
`$ErrorActionPreference = 'Stop'
`$token = (Get-Content -Raw '$($KeyFile.Replace("'", "''"))').Trim()
[Console]::Out.Write(`$token)
"@ | Set-Content -Path $TokenHelper -Encoding utf8

if (Test-Path $ConfigFile) {
    Copy-Item -Force $ConfigFile "$ConfigFile.bak"
    $lines = Get-Content $ConfigFile
} else {
    $lines = @()
}

$result = [System.Collections.Generic.List[string]]::new()
$seenTable = $false
$insertedProvider = $false
$skipProxy = $false

foreach ($line in $lines) {
    if ($line -match '^\s*\[') {
        if ($line -match '^\s*\[model_providers\.codex_proxy(?:\.|\])') {
            $skipProxy = $true
            $seenTable = $true
            continue
        }
        $skipProxy = $false
        if (-not $insertedProvider) {
            $result.Add('model_provider = "codex_proxy"')
            $result.Add('')
            $insertedProvider = $true
        }
        $seenTable = $true
    }
    if ($skipProxy) { continue }
    if (-not $seenTable -and $line -match '^\s*model_provider\s*=') { continue }
    $result.Add($line)
}

if (-not $insertedProvider) {
    $result.Add('model_provider = "codex_proxy"')
    $result.Add('')
}

$helperPath = $TokenHelper.Replace('\', '/')
$result.Add('')
$result.Add('[model_providers.codex_proxy]')
$result.Add('name = "OpenAI"')
$result.Add("base_url = `"$ModelBaseUrl`"")
$result.Add('wire_api = "responses"')
$result.Add('supports_websockets = false')
$result.Add('stream_idle_timeout_ms = 900000')
$result.Add('')
$result.Add('[model_providers.codex_proxy.auth]')
$result.Add('command = "powershell.exe"')
$result.Add("args = [`"-NoProfile`", `"-ExecutionPolicy`", `"Bypass`", `"-File`", `"$helperPath`"]")
$result.Add('refresh_interval_ms = 0')
$result | Set-Content -Path $ConfigFile -Encoding utf8

# Remove only files created by older versions of this installer.
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $CodexDir 'proxy.config.toml')
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $CodexDir 'codex-proxy-real-cli')
$legacyWrapper = Join-Path $BinDir 'codex-proxy.ps1'
if (Test-Path $legacyWrapper) {
    $legacyText = Get-Content -Raw $legacyWrapper
    if ($legacyText -match '--profile proxy') {
        Remove-Item -Force $legacyWrapper
    }
}

Write-Host ''
Write-Host 'Codex CLI configured to use Codex Proxy.'
Write-Host "  Config: $ConfigFile"
Write-Host "  API:    $ModelBaseUrl"
Write-Host ''
Write-Host 'Run: codex'

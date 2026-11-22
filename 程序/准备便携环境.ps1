# Windows PowerShell 5.1+，仅在项目目录内准备 x64 运行环境。
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtime = Join-Path $project '.runtime'
$rootPrefix = $project.TrimEnd('\') + '\'
$pythonUrl = 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.zip'
# https://www.python.org/ftp/python/3.13.17/windows-3.13.17.json，pythoncore-3.13-64。
# 此包由 Python 官方安装管理器使用，已包含 pip。
$pythonHash = 'b26ca4b4ee51e157836049934d2b8eabd55939be296f3130254251b6df96ec2f'
$staging = $null
$mutex = $null
$acquired = $false
$exitCode = 0

function Test-Ready([string]$folder) {
    $exe = Join-Path $folder 'python.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { return $false }
    & $exe -I -B -c "import numpy, pandas, requests, sqlite3, ssl" 2>$null
    return $LASTEXITCODE -eq 0
}

try {
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
    if ($arch -ne 'AMD64') { throw '自动准备环境支持 x64 Windows。其他架构请安装适配本机的 Python 3.13，再打开启动器。' }

    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $key = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($project.ToUpperInvariant()))).Replace('-', '')
    } finally { $sha.Dispose() }
    $mutex = [Threading.Mutex]::new($false, "Local\MarketFundsPython-$key")
    try { $acquired = $mutex.WaitOne([TimeSpan]::FromMinutes(12)) }
    catch [Threading.AbandonedMutexException] { $acquired = $true }
    if (-not $acquired) { throw '另一个窗口仍在准备运行环境，请稍后再次启动。' }

    if (Test-Ready $runtime) { return }
    if (Test-Path -LiteralPath $runtime) { throw '.runtime 已存在但无法使用。请将其改名备份，再打开启动器。' }
    $staging = Join-Path $project ('.runtime-stage-' + [Guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($staging)
    $archive = Join-Path $staging 'python.zip'
    $stageRuntime = Join-Path $staging 'runtime'
    Write-Host '首次使用，正在下载 Python 官方 x64 运行环境（约 31 MB）…'
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $pythonUrl -OutFile $archive -TimeoutSec 180
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $pythonHash) {
        throw '下载的 Python 文件与官方 SHA256 不符，请检查网络后重试。'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $stageRuntime)
    $exe = Join-Path $stageRuntime 'python.exe'
    Write-Host '正在从 PyPI 安装计算与联网组件…'
    & $exe -I -B -X utf8 -m pip --isolated --disable-pip-version-check install --no-input --no-cache-dir --no-compile --no-warn-script-location --only-binary ':all:' --index-url 'https://pypi.org/simple' -r (Join-Path $project 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw '依赖下载或安装失败，请检查网络并再次打开启动器。' }
    if (-not (Test-Ready $stageRuntime)) { throw '运行环境未通过导入检查。' }
    if (-not ([IO.Path]::GetFullPath($stageRuntime)).StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        -not ([IO.Path]::GetFullPath($runtime)).StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw '运行环境路径超出项目目录。'
    }
    [IO.Directory]::Move($stageRuntime, $runtime)
    Write-Host '运行环境已准备好。'
} catch {
    Write-Host $_.Exception.Message
    Write-Host '也可从 https://www.python.org/downloads/windows/ 安装 Python 3.13，再双击启动器。'
    $exitCode = 1
} finally {
    if ($staging -and (Test-Path -LiteralPath $staging -PathType Container)) {
        $verified = (Get-Item -LiteralPath $staging).FullName
        if ([IO.Path]::GetDirectoryName($verified) -eq $project -and
            [IO.Path]::GetFileName($verified) -match '^\.runtime-stage-[0-9a-f]{32}$') {
            Remove-Item -LiteralPath $verified -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if ($acquired) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
exit $exitCode

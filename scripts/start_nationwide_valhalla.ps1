param(
    [switch]$VerifyHash,
    [ValidateRange(1, 12)][int]$ServerThreads = 4,
    [ValidateRange(4, 24)][int]$MemoryGiB = 14
)

$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dataDirectory = Join-Path $repository 'var\road_nationwide\us_graph'
$download = Join-Path $repository 'var\road_nationwide\us-2026-10-09.osm.pbf'
$source = Join-Path $dataDirectory 'us-2026-10-09.osm.pbf'
$expectedBytes = 12196456372
$expectedMd5 = '0befc76d07878c8a68f1ed85948ea7a9'
$container = 'fuel-planner-valhalla-us'
$image = 'ghcr.io/valhalla/valhalla-scripted@sha256:b8c4c818575668aa6dc459ce62f5fc1b12832e39992d84aa57318fa6417253ad'

if (-not (Test-Path -LiteralPath $download)) {
    throw "Missing pinned US road source: $download"
}
if ((Get-Item -LiteralPath $download).Length -ne $expectedBytes) {
    throw 'US road source is incomplete or has changed size'
}
if ($VerifyHash) {
    $actual = (Get-FileHash -LiteralPath $download -Algorithm MD5).Hash.ToLowerInvariant()
    if ($actual -ne $expectedMd5) {
        throw "US road source MD5 differs from the pinned Geofabrik digest: $actual"
    }
}
$freeBytes = (Get-PSDrive -Name C).Free
if ($freeBytes -lt 80GB) {
    $freeGiB = [math]::Round($freeBytes / 1GB, 1)
    throw "The guarded full-US build needs at least 80 GiB free; C: has $freeGiB GiB"
}
New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null
if (-not (Test-Path -LiteralPath $source)) {
    New-Item -ItemType HardLink -Path $source -Target $download | Out-Null
}
$sampleConfig = Join-Path $repository 'var\road_nationwide\valhalla_midwest_probe\valhalla.json'
$nationalConfig = Join-Path $dataDirectory 'valhalla.json'
if (-not (Test-Path -LiteralPath $nationalConfig)) {
    if (-not (Test-Path -LiteralPath $sampleConfig)) {
        throw 'Pinned Valhalla config is unavailable; run the Midwest probe first'
    }
    $config = Get-Content -LiteralPath $sampleConfig -Raw | ConvertFrom-Json
    $config.service_limits.auto.max_matrix_distance = 900000
    $config | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $nationalConfig -Encoding utf8
}
$existing = docker container ls -a --filter "name=^/$container$" --format '{{.Names}}'
if ($LASTEXITCODE -ne 0) {
    throw 'Docker engine is unavailable'
}
if ($existing) {
    throw "Container $container already exists; inspect or resume it rather than creating another build"
}
docker run -d --name $container --memory "${MemoryGiB}g" -p 127.0.0.1:8004:8002 `
    -v "${dataDirectory}:/custom_files" `
    -e build_tar=False -e build_elevation=False -e build_transit=False `
    -e use_default_speeds_config=False -e use_tiles_ignore_pbf=True `
    -e "server_threads=$ServerThreads" $image
if ($LASTEXITCODE -ne 0) {
    throw 'Docker could not start the nationwide Valhalla build'
}
Write-Output "Started $container from the pinned US road source; follow with docker logs $container --follow"
$monitor = Join-Path $PSScriptRoot 'monitor_nationwide_disk.ps1'
$watchdog = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$monitor`""
) -WindowStyle Hidden -PassThru
Write-Output "Disk monitor PID $($watchdog.Id) will stop this build if C: drops below 15 GiB free"

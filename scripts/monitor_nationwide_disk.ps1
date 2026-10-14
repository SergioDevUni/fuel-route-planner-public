param(
    [string]$Container = 'fuel-planner-valhalla-us',
    [int]$MinimumFreeGiB = 15
)

$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$log = Join-Path $repository 'var\road_nationwide\build_monitor.log'
while ($true) {
    Start-Sleep -Seconds 15
    $running = docker container ls --filter "name=^/$Container$" --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0) {
        Add-Content -LiteralPath $log -Value "$(Get-Date -Format o) docker unavailable; monitor exiting"
        break
    }
    if ($running -ne $Container) {
        Add-Content -LiteralPath $log -Value "$(Get-Date -Format o) build container stopped; monitor exiting"
        break
    }
    $freeGiB = (Get-PSDrive -Name C).Free / 1GB
    if ($freeGiB -lt $MinimumFreeGiB) {
        Add-Content -LiteralPath $log -Value "$(Get-Date -Format o) C: free $([math]::Round($freeGiB, 2)) GiB; stopping $Container"
        docker stop $Container | Out-Null
        break
    }
}

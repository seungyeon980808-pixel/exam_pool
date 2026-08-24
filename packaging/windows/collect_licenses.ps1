[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath,
    [Parameter(Mandatory = $true)]
    [string]$OcrRuntime
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$buildRoot = Join-Path $projectRoot 'build'
$legalOutput = Join-Path $buildRoot 'legal'
$licenseOutput = Join-Path $legalOutput 'licenses'

if (Test-Path -LiteralPath $legalOutput) {
    $resolvedLegalOutput = (Resolve-Path -LiteralPath $legalOutput).Path
    if (-not $resolvedLegalOutput.StartsWith($buildRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "라이선스 출력 경로가 build 디렉터리 밖에 있습니다: $resolvedLegalOutput"
    }
    Remove-Item -LiteralPath $resolvedLegalOutput -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $licenseOutput | Out-Null

$sitePackagesOutput = @(& $PythonPath -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
$sitePackages = $sitePackagesOutput[-1].Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $sitePackages)) {
    throw "Python site-packages 경로를 확인하지 못했습니다: $sitePackages"
}

$packageRoots = @($sitePackages, $OcrRuntime) | Select-Object -Unique
$packages = [ordered]@{}

function Get-MetadataValue {
    param(
        [string[]]$Metadata,
        [string]$Field
    )
    $line = $Metadata | Where-Object { $_ -like "${Field}: *" } | Select-Object -First 1
    if ($null -eq $line) {
        return ''
    }
    return ([string]$line -replace "^${Field}:\s*", '').Trim()
}

foreach ($packageRoot in $packageRoots) {
    if (-not (Test-Path -LiteralPath $packageRoot)) {
        continue
    }
    foreach ($distInfo in Get-ChildItem -LiteralPath $packageRoot -Directory -Filter '*.dist-info') {
        $metadataPath = Join-Path $distInfo.FullName 'METADATA'
        if (-not (Test-Path -LiteralPath $metadataPath)) {
            continue
        }
        $metadata = Get-Content -LiteralPath $metadataPath
        $name = Get-MetadataValue -Metadata $metadata -Field 'Name'
        $version = Get-MetadataValue -Metadata $metadata -Field 'Version'
        $license = Get-MetadataValue -Metadata $metadata -Field 'License-Expression'
        if (-not $license) {
            $license = Get-MetadataValue -Metadata $metadata -Field 'License'
        }
        if (-not $license) {
            $license = 'See bundled license files'
        }
        $homepage = Get-MetadataValue -Metadata $metadata -Field 'Home-page'
        if (-not $homepage) {
            $homepage = Get-MetadataValue -Metadata $metadata -Field 'Project-URL'
            $homepage = $homepage -replace '^Homepage,\s*', ''
        }

        $key = "$name==$version"
        if (-not $packages.Contains($key)) {
            $packages[$key] = [pscustomobject]@{
                Name = $name
                Version = $version
                License = $license
                Homepage = $homepage
            }
        }

        $safePackageName = ($key -replace '[^0-9A-Za-z._-]', '_')
        $packageLicenseOutput = Join-Path $licenseOutput $safePackageName
        $licenseFiles = @(
            Get-ChildItem -LiteralPath $distInfo.FullName -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match '^(LICENSE|COPYING|NOTICE)' }
            Get-ChildItem -LiteralPath (Join-Path $distInfo.FullName 'licenses') -File -Recurse -ErrorAction SilentlyContinue
        )
        if ($licenseFiles.Count -gt 0) {
            New-Item -ItemType Directory -Force -Path $packageLicenseOutput | Out-Null
            foreach ($licenseFile in $licenseFiles) {
                Copy-Item -LiteralPath $licenseFile.FullName -Destination (Join-Path $packageLicenseOutput $licenseFile.Name) -Force
            }
        }
    }
}

$noticeLines = @(
    '# Bundled Python package licenses',
    '',
    'This inventory is generated from the exact Python and OCR runtimes used by the Windows build.',
    '',
    '| Package | Version | License | Homepage |',
    '|---|---:|---|---|'
)
foreach ($package in $packages.Values | Sort-Object Name, Version) {
    $license = $package.License -replace '\|', '\|'
    $homepage = $package.Homepage -replace '\|', '\|'
    $noticeLines += "| $($package.Name) | $($package.Version) | $license | $homepage |"
}
$noticeLines | Set-Content -LiteralPath (Join-Path $legalOutput 'BUNDLED_PACKAGES.md') -Encoding utf8

Write-Host "라이선스 인벤토리: $legalOutput"

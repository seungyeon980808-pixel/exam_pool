$ErrorActionPreference = 'Stop'

$repository = Split-Path -Parent $MyInvocation.MyCommand.Path
$overlayRoot = Join-Path $repository 'data\hwppalette_additive_root'
$library = Join-Path $overlayRoot 'data\library.json'
$cli = Join-Path $overlayRoot 'hwp_palette\cli.py'

if (-not (Test-Path -LiteralPath $library -PathType Leaf)) {
    throw "Additive HwpPalette library is missing: $library"
}
if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {
    throw "Additive HwpPalette runtime is missing: $cli"
}

# HwpPaletteProvider reads this only when the ExamPool process is constructed.
# This launcher intentionally does not alter the user's persistent environment.
$env:EXAMPOOL_HWPPAL_ROOT = $overlayRoot

& (Join-Path $repository 'run.bat')
exit $LASTEXITCODE

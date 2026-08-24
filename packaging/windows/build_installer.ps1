[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')]
    [string]$Version = '0.1.0',
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$specPath = Join-Path $PSScriptRoot 'ExamPoolHwpConverter.spec'
$issPath = Join-Path $PSScriptRoot 'ExamPoolHwpConverter.iss'
$licenseCollector = Join-Path $PSScriptRoot 'collect_licenses.ps1'
$distPath = Join-Path $projectRoot 'dist\ExamPool-HWP-Converter'
$installerOutput = Join-Path $projectRoot 'dist\installer'
$ocrRuntime = Join-Path $projectRoot 'data\pdf_hwp_ocr_runtime'

if (-not $PythonPath) {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $PythonPath = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { 'python' }
}

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath (Join-Path $ocrRuntime 'paddleocr')) -or
        -not (Test-Path -LiteralPath (Join-Path $ocrRuntime 'paddle'))) {
        $ocrRuntime = Join-Path $projectRoot 'build\pdf_hwp_ocr_runtime'
        $uvCommand = Get-Command 'uv' -ErrorAction SilentlyContinue
        if (-not $uvCommand) {
            throw 'OCR 런타임 준비에 필요한 uv 명령을 찾지 못했습니다.'
        }
        New-Item -ItemType Directory -Force -Path $ocrRuntime | Out-Null
        & $uvCommand.Source pip install --python $PythonPath --target $ocrRuntime `
            'paddleocr==3.7.0' 'paddlepaddle==3.3.1'
        if ($LASTEXITCODE -ne 0) {
            throw "OCR 런타임 준비에 실패했습니다. 종료 코드: $LASTEXITCODE"
        }
    }
    $env:EXAMPOOL_OCR_RUNTIME = $ocrRuntime

    & $licenseCollector -PythonPath $PythonPath -OcrRuntime $ocrRuntime
    if ($LASTEXITCODE -ne 0) {
        throw "제3자 라이선스 수집에 실패했습니다. 종료 코드: $LASTEXITCODE"
    }
    $env:EXAMPOOL_LEGAL_DIR = Join-Path $projectRoot 'build\legal'

    & $PythonPath -m PyInstaller --noconfirm --clean $specPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 빌드에 실패했습니다. 종료 코드: $LASTEXITCODE"
    }

    $isccCommand = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    $isccPath = if ($isccCommand) { $isccCommand.Source } else { '' }
    if (-not $isccPath) {
        $knownPaths = @(
            (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
        )
        foreach ($knownPath in $knownPaths) {
            if (Test-Path -LiteralPath $knownPath) {
                $isccPath = $knownPath
                break
            }
        }
    }
    if (-not $isccPath) {
        throw 'Inno Setup 6을 찾지 못했습니다. ISCC.exe를 설치한 뒤 다시 실행해 주세요.'
    }

    New-Item -ItemType Directory -Force -Path $installerOutput | Out-Null
    & $isccPath "/DMyAppVersion=$Version" "/DSourceDir=$distPath" "/DInstallerOutputDir=$installerOutput" $issPath
    if ($LASTEXITCODE -ne 0) {
        throw "설치 파일 생성에 실패했습니다. 종료 코드: $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Write-Host "설치 파일: $installerOutput\ExamPool-HWP-Converter-Setup-$Version.exe"

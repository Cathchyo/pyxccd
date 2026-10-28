param(
    [ValidateSet('Setup', 'Copy', 'Audit', 'Prepare', 'Smoke', 'Tune', 'Test', 'Compare')]
    [string]$Stage = 'Audit',
    [string]$Python = '',
    [string]$Source = 'C:\personal\Jason\reviewed-hls-51rtq\data\temporal_disturbance_51RTQ_v1',
    [string]$Snapshot = 'datasets/my_project/raw/hls51rtq_snapshot',
    [string]$Dataset = 'datasets/my_project/processed/cold_full_v1',
    [string]$History = '',
    [string]$Output = '',
    [string]$Lock = '',
    [string]$ColdPredictions = '',
    [string]$DeepPredictions = '',
    [switch]$Pilot,
    [switch]$AcceptSourceQC,
    [int]$Limit = 32
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if ($Stage -eq 'Setup') {
        if (-not (Test-Path -LiteralPath '.venv-cold/Scripts/python.exe')) {
            & uv venv --python 3.11 .venv-cold
            if ($LASTEXITCODE -ne 0) { throw 'Failed to create environment' }
        }
        & uv pip install --python .venv-cold/Scripts/python.exe --only-binary :all: -r experiments/cold_comparison/requirements-lock.txt
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install environment' }
        return
    }
    if (-not $Python) { $Python = Join-Path $PSScriptRoot '.venv-cold/Scripts/python.exe' }
    $taskArgs = @('experiments/cold_comparison/pipeline.py')
    switch ($Stage) {
        'Copy' { $taskArgs += @('snapshot', '--source', $Source, '--output', $Snapshot) }
        'Audit' {
            if (-not $Output) { $Output = 'docs/my_project/reports/audit_' + (Get-Date -Format 'yyyyMMdd_HHmmss') }
            $taskArgs += @('audit', '--snapshot', $Snapshot, '--output', $Output)
            if ($History) { $taskArgs += @('--history', $History) }
        }
        'Prepare' {
            if (-not $Output) { throw 'Prepare requires -Output with a new dataset directory' }
            $taskArgs += @('prepare', '--snapshot', $Snapshot, '--output', $Output)
            if ($History) { $taskArgs += @('--history', $History) }
            if ($Pilot) { $taskArgs += '--pilot' }
            if ($AcceptSourceQC) { $taskArgs += '--accept-source-qc' }
        }
        'Smoke' {
            if (-not $Output) { $Output = 'datasets/my_project/results/smoke_' + (Get-Date -Format 'yyyyMMdd_HHmmss') }
            $taskArgs += @('smoke', '--dataset', $Dataset, '--output', $Output, '--limit', [string]$Limit)
        }
        'Tune' {
            if (-not $Output) { throw 'Tune requires -Output' }
            $taskArgs += @('tune', '--dataset', $Dataset, '--output', $Output)
        }
        'Test' {
            if (-not $Output -or -not $Lock) { throw 'Test requires -Output and -Lock' }
            $taskArgs += @('test', '--dataset', $Dataset, '--lock', $Lock, '--output', $Output)
        }
        'Compare' {
            if (-not $Output -or -not $ColdPredictions -or -not $DeepPredictions) { throw 'Compare requires -Output, -ColdPredictions and -DeepPredictions' }
            $taskArgs += @('compare', '--dataset', $Dataset, '--cold', $ColdPredictions, '--deep', $DeepPredictions, '--output', $Output)
        }
    }
    & $Python @taskArgs
    if ($LASTEXITCODE -ne 0) { throw ('COLD stage failed: ' + $Stage) }
}
finally { Pop-Location }

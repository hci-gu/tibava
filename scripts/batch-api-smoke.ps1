param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$Username = "test@email.com",
    [string]$Password = "password123",
    [string]$Preset = "default_batch_analysis",
    [string[]]$VideoPaths = @(),
    [int]$TimeoutSeconds = 900,
    [switch]$SkipPreset
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$RunId = [guid]::NewGuid().ToString("N").Substring(0, 8)
$WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) "tibava-batch-smoke-$RunId"
$CookieJar = Join-Path $WorkDir "cookies.txt"

function Write-Step($Message) {
    Write-Host "==> $Message"
}

function Invoke-CurlJson {
    param([string[]]$Arguments)

    $output = & curl.exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "curl failed with exit code $LASTEXITCODE"
    }
    $jsonText = ($output | Out-String).Trim()
    if (-not $jsonText) {
        throw "Empty JSON response"
    }
    return $jsonText | ConvertFrom-Json
}

function Get-CsrfToken {
    $csrf = Select-String -Path $CookieJar -Pattern "csrftoken\s+(.+)$" |
        Select-Object -Last 1 |
        ForEach-Object { $_.Matches[0].Groups[1].Value }
    if (-not $csrf) {
        throw "Could not read csrftoken from $CookieJar"
    }
    return $csrf
}

function Invoke-ApiGet($Path) {
    return Invoke-CurlJson -Arguments @(
        "-sS",
        "-b", $CookieJar,
        "$BaseUrl$Path"
    )
}

function Invoke-ApiPostJson($Path, $Body) {
    $csrf = Get-CsrfToken
    $bodyPath = Join-Path $WorkDir ("body-" + [guid]::NewGuid().ToString("N") + ".json")
    Set-Content -Path $bodyPath -Value ($Body | ConvertTo-Json -Compress) -NoNewline
    try {
        return Invoke-CurlJson -Arguments @(
            "-sS",
            "-b", $CookieJar,
            "-c", $CookieJar,
            "-H", "Content-Type: application/json",
            "-H", "X-CSRFToken: $csrf",
            "--data-binary", "@$bodyPath",
            "$BaseUrl$Path"
        )
    } finally {
        if (Test-Path $bodyPath) {
            Remove-Item -LiteralPath $bodyPath -Force
        }
    }
}

function Assert-Ok($Response, $Context) {
    if ($Response.status -ne "ok") {
        $json = $Response | ConvertTo-Json -Compress
        throw "$Context failed: $json"
    }
}

function Get-Batch($BatchId) {
    $response = Invoke-ApiGet "/video/batch/get?id=$BatchId"
    Assert-Ok $response "Get batch $BatchId"
    return $response.entry
}

function Wait-BatchIngestTerminal($BatchId, $ExpectedReadyMinimum = 1) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastReadyCount = 0
    do {
        $batch = Get-Batch $BatchId
        $openItems = @($batch.items | Where-Object {
            $_.ingest_status -eq "PENDING" -or $_.ingest_status -eq "INGESTING"
        })
        $readyItems = @($batch.items | Where-Object { $_.ingest_status -eq "READY" })
        $lastReadyCount = $readyItems.Count
        if ($openItems.Count -eq 0) {
            if ($lastReadyCount -ge $ExpectedReadyMinimum) {
                return $batch
            }
        }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for batch $BatchId ingest to finish with $ExpectedReadyMinimum ready videos; last ready count was $lastReadyCount"
}

function Wait-BatchPresetTerminal($BatchId, $ExpectedReadyCount) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $batch = Get-Batch $BatchId
        $runs = @($batch.plugin_runs)
        $openRuns = @($runs | Where-Object {
            $_.status -eq "PENDING" -or $_.status -eq "RUNNING"
        })
        if (
            $runs.Count -ge $ExpectedReadyCount -and
            $openRuns.Count -eq 0 -and
            $batch.status -ne "RUNNING"
        ) {
            $doneRuns = @($runs | Where-Object { $_.status -eq "DONE" })
            if ($doneRuns.Count -lt 1) {
                throw "Batch $BatchId preset finished without any completed plugin steps"
            }
            return $batch
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for batch $BatchId preset to finish"
}

function Get-DefaultVideoPaths {
    $response = Invoke-ApiGet "/video/list"
    Assert-Ok $response "List videos"

    $paths = @()
    foreach ($video in $response.entries) {
        if (-not $video.file -or -not $video.ext) {
            continue
        }
        $relativePath = Join-Path $video.file.Substring(0, 2) (
            Join-Path $video.file.Substring(2, 2) ($video.file + $video.ext)
        )
        $path = Join-Path $RepoRoot (Join-Path "data/media" $relativePath)
        if (Test-Path $path) {
            $paths += $path
        }
        if ($paths.Count -ge 2) {
            break
        }
    }

    return $paths
}

function Copy-SampleVideos($Paths) {
    $looseDir = Join-Path $WorkDir "loose"
    $zipRoot = Join-Path $WorkDir "zip-root"
    New-Item -ItemType Directory -Force -Path $looseDir, (Join-Path $zipRoot "folder-a"), (Join-Path $zipRoot "folder-b/nested") | Out-Null

    $resolved = @($Paths | ForEach-Object { Resolve-Path $_ })
    if ($resolved.Count -lt 2) {
        throw "Need at least two sample videos. Pass -VideoPaths or seed videos for $Username."
    }

    $looseA = Join-Path $looseDir "clip-a.mp4"
    $looseB = Join-Path $looseDir "clip-b.mp4"
    Copy-Item -LiteralPath $resolved[0] -Destination $looseA
    Copy-Item -LiteralPath $resolved[1] -Destination $looseB

    $zipA = Join-Path $zipRoot "folder-a/clip-a.mp4"
    $zipB = Join-Path $zipRoot "folder-b/nested/clip-b.mp4"
    Copy-Item -LiteralPath $resolved[0] -Destination $zipA
    Copy-Item -LiteralPath $resolved[1] -Destination $zipB
    Set-Content -Path (Join-Path $zipRoot "folder-b/notes.txt") -Value "unsupported"

    $zipPath = Join-Path $WorkDir "nested.zip"
    Compress-Archive -Path (Join-Path $zipRoot "*") -DestinationPath $zipPath

    return @{
        Loose = @($looseA, $looseB)
        Zip = $zipPath
    }
}

try {
    New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
    Write-Step "Using work directory $WorkDir"

    Write-Step "Creating authenticated session for $Username"
    $csrfResponse = Invoke-CurlJson -Arguments @("-sS", "-c", $CookieJar, "$BaseUrl/user/csrf")
    Assert-Ok $csrfResponse "CSRF"
    $loginResponse = Invoke-ApiPostJson "/user/login" @{
        params = @{
            name = $Username
            password = $Password
        }
    }
    Assert-Ok $loginResponse "Login"

    if ($VideoPaths.Count -eq 0) {
        Write-Step "Finding sample videos from existing account media"
        $VideoPaths = Get-DefaultVideoPaths
    }
    $samples = Copy-SampleVideos $VideoPaths

    Write-Step "Uploading loose multi-file batch"
    $pathsJson = '["folder-a/clip-a.mp4","folder-b/nested/clip-b.mp4"]'
    $pathsFile = Join-Path $WorkDir "paths.json"
    Set-Content -Path $pathsFile -Value $pathsJson -NoNewline
    $csrf = Get-CsrfToken
    $looseResponse = Invoke-CurlJson -Arguments @(
        "-sS",
        "-b", $CookieJar,
        "-c", $CookieJar,
        "-H", "X-CSRFToken: $csrf",
        "-F", "name=Smoke loose $RunId",
        "-F", "auto_run_preset=false",
        "-F", "paths=<$pathsFile",
        "-F", "files=@$($samples.Loose[0]);filename=clip-a.mp4",
        "-F", "files=@$($samples.Loose[1]);filename=clip-b.mp4",
        "$BaseUrl/video/batch/upload"
    )
    Assert-Ok $looseResponse "Loose upload"
    $looseBatch = Wait-BatchIngestTerminal $looseResponse.batch_id 2
    $loosePaths = @($looseBatch.items | Sort-Object original_path | ForEach-Object { $_.original_path })
    if (($loosePaths -join "|") -ne "folder-a/clip-a.mp4|folder-b/nested/clip-b.mp4") {
        throw "Loose upload did not preserve expected paths: $($loosePaths -join ', ')"
    }

    if (-not $SkipPreset) {
        Write-Step "Running preset $Preset against loose batch"
        $runResponse = Invoke-ApiPostJson "/video/batch/run-preset" @{
            id = $looseResponse.batch_id
            preset = $Preset
        }
        Assert-Ok $runResponse "Run preset"
        $looseBatch = Wait-BatchPresetTerminal $looseResponse.batch_id ([int]$looseBatch.ready_count)
    }

    Write-Step "Uploading nested zip batch with an unsupported file"
    $csrf = Get-CsrfToken
    $zipResponse = Invoke-CurlJson -Arguments @(
        "-sS",
        "-b", $CookieJar,
        "-c", $CookieJar,
        "-H", "X-CSRFToken: $csrf",
        "-F", "name=Smoke zip $RunId",
        "-F", "auto_run_preset=false",
        "-F", "zip=@$($samples.Zip);filename=nested.zip",
        "$BaseUrl/video/batch/upload"
    )
    Assert-Ok $zipResponse "Zip upload"
    $zipBatch = Wait-BatchIngestTerminal $zipResponse.batch_id 2
    $zipPaths = @($zipBatch.items | Sort-Object original_path | ForEach-Object { $_.original_path })
    foreach ($expected in @("folder-a/clip-a.mp4", "folder-b/nested/clip-b.mp4", "folder-b/notes.txt")) {
        if ($zipPaths -notcontains $expected) {
            throw "Zip upload is missing expected path $expected"
        }
    }
    $unsupported = @($zipBatch.items | Where-Object {
        $_.original_path -eq "folder-b/notes.txt" -and $_.ingest_error -eq "wrong_file_extension"
    })
    if ($unsupported.Count -ne 1) {
        throw "Zip upload did not report wrong_file_extension for notes.txt"
    }

    Write-Step "Retrying failed ingest work"
    $retryResponse = Invoke-ApiPostJson "/video/batch/retry-failed" @{
        id = $zipResponse.batch_id
    }
    Assert-Ok $retryResponse "Retry failed ingest"

    Write-Step "Uploading then cancelling a disposable batch"
    $csrf = Get-CsrfToken
    $cancelResponse = Invoke-CurlJson -Arguments @(
        "-sS",
        "-b", $CookieJar,
        "-c", $CookieJar,
        "-H", "X-CSRFToken: $csrf",
        "-F", "name=Smoke cancel $RunId",
        "-F", "auto_run_preset=false",
        "-F", "files=@$($samples.Loose[0]);filename=cancel.mp4",
        "$BaseUrl/video/batch/upload"
    )
    Assert-Ok $cancelResponse "Cancel upload"
    $cancelApiResponse = Invoke-ApiPostJson "/video/batch/cancel" @{
        id = $cancelResponse.batch_id
    }
    Assert-Ok $cancelApiResponse "Cancel batch"
    $cancelBatch = Get-Batch $cancelResponse.batch_id
    if ($cancelBatch.status -ne "CANCELLED") {
        throw "Expected cancelled batch status, got $($cancelBatch.status)"
    }

    Write-Step "Deleting disposable batches"
    foreach ($batchId in @($looseResponse.batch_id, $zipResponse.batch_id, $cancelResponse.batch_id)) {
        $deleteResponse = Invoke-ApiPostJson "/video/batch/delete" @{ id = $batchId }
        Assert-Ok $deleteResponse "Delete batch $batchId"
    }

    Write-Host "Batch API smoke passed"
} finally {
    if (Test-Path $WorkDir) {
        Remove-Item -LiteralPath $WorkDir -Recurse -Force
    }
}

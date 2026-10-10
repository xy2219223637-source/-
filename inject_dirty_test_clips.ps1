param(
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [Parameter(Mandatory = $true)]
    [string]$Ffmpeg
)

$ErrorActionPreference = 'Stop'

$finishedDir = Join-Path $SourceRoot '成片'
$assetDir = Join-Path $SourceRoot '素材\04_使用体验与实测'
$dirtyA = Join-Path $assetDir '10_03_厚纸擦拭污渍.mp4'

foreach ($required in @($finishedDir, $dirtyA, $Ffmpeg)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required path not found: $required"
    }
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

function Get-MediaInfo([string]$Path) {
    $probeText = (& $Ffmpeg -hide_banner -i $Path 2>&1 | Out-String)
    $durationMatch = [regex]::Match($probeText, 'Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)')
    $videoMatch = [regex]::Match($probeText, 'Video:.*?,\s+(\d+)x(\d+)[\s,]')
    if (-not $durationMatch.Success -or -not $videoMatch.Success) {
        throw "Unable to read media info: $Path"
    }
    $duration = ([double]$durationMatch.Groups[1].Value * 3600) +
                ([double]$durationMatch.Groups[2].Value * 60) +
                [double]$durationMatch.Groups[3].Value
    [pscustomobject]@{
        Duration = $duration
        Width = [int]$videoMatch.Groups[1].Value
        Height = [int]$videoMatch.Groups[2].Value
    }
}

$manifest = [System.Collections.Generic.List[object]]::new()
$files = Get-ChildItem -LiteralPath $finishedDir -Filter '*.mp4' | Sort-Object Name

foreach ($file in $files) {
    $info = Get-MediaInfo $file.FullName
    $t1 = [math]::Round($info.Duration * 0.35, 3)
    $t2 = [math]::Round($info.Duration * 0.70, 3)
    $w = $info.Width
    $h = $info.Height
    $output = Join-Path $OutputDir ($file.BaseName + '_脏片段测试版.mp4')

    if (Test-Path -LiteralPath $output) {
        throw "Refusing to overwrite existing output: $output"
    }

    $filter = @"
[0:v]trim=start=0:end=$t1,setpts=PTS-STARTPTS,fps=30,format=yuv420p[v0];
[0:a]atrim=start=0:end=$t1,asetpts=PTS-STARTPTS,aresample=44100[a0];
[1:v]trim=start=6.8:duration=4.0,setpts=PTS-STARTPTS,scale=${w}:${h}:force_original_aspect_ratio=increase,crop=${w}:${h},fps=30,format=yuv420p[v1];
[1:a]atrim=start=6.8:duration=4.0,asetpts=PTS-STARTPTS,aresample=44100[a1];
[0:v]trim=start=${t1}:end=${t2},setpts=PTS-STARTPTS,fps=30,format=yuv420p[v2];
[0:a]atrim=start=${t1}:end=${t2},asetpts=PTS-STARTPTS,aresample=44100[a2];
[1:v]trim=start=6.8:duration=4.0,setpts=PTS-STARTPTS,scale=${w}:${h}:force_original_aspect_ratio=increase,crop=${w}:${h},fps=30,format=yuv420p[v3];
[1:a]atrim=start=6.8:duration=4.0,asetpts=PTS-STARTPTS,aresample=44100[a3];
[0:v]trim=start=$t2,setpts=PTS-STARTPTS,fps=30,format=yuv420p[v4];
[0:a]atrim=start=$t2,asetpts=PTS-STARTPTS,aresample=44100[a4];
[v0][a0][v1][a1][v2][a2][v3][a3][v4][a4]concat=n=5:v=1:a=1[v][a]
"@ -replace "`r?`n", ''

    & $Ffmpeg -hide_banner -loglevel error -stats -i $file.FullName -i $dirtyA `
        -filter_complex $filter -map '[v]' -map '[a]' `
        -c:v libx264 -preset medium -crf 18 -c:a aac -b:a 128k -movflags +faststart `
        $output

    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $output)) {
        throw "Failed to render: $($file.Name)"
    }

    $renderedInfo = Get-MediaInfo $output
    $manifest.Add([pscustomobject]@{
        output_file = Split-Path -Leaf $output
        original_file = $file.Name
        original_duration_s = $info.Duration
        first_insert_at_s = $t1
        first_insert_source = '10_03_厚纸擦拭污渍.mp4 @ 6.8s, 4.0s'
        second_insert_at_original_s = $t2
        second_insert_at_output_s = [math]::Round($t2 + 4.0, 3)
        second_insert_source = '10_03_厚纸擦拭污渍.mp4 @ 6.8s, 4.0s'
        output_duration_s = $renderedInfo.Duration
        resolution = "${w}x${h}"
    })
}

$manifestPath = Join-Path $OutputDir '插入清单.csv'
$manifest | Export-Csv -LiteralPath $manifestPath -NoTypeInformation -Encoding utf8
$manifest | Format-Table -AutoSize

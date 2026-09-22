# RockCleanup.ps1 — bots clean up after themselves.
# (Operator rule: keep 1 week, then delete ONLY what the bots generate that
# eats disk: videos, images, generated text files. NEVER touch logins, browser
# profiles, scripts, or state — the operator does not want to re-login to anything.)
$ErrorActionPreference = 'SilentlyContinue'
$cutoff = (Get-Date).AddHours(-168)
$logFile = 'C:\RockVideos\work\cleanup.log'
# Allowlist: only generated media + text. Everything else is left alone.
$targetExt = @('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp',
               '.mp4', '.mov', '.avi', '.mkv', '.webm',
               '.txt', '.md')
$protectDirs = @('C:\RockVideos\work\gemini-profile')

function Clean-Dir($path) {
    $count = 0; $bytes = 0
    if (-not (Test-Path $path)) { return @(0, 0) }
    Get-ChildItem -Path $path -Recurse -File | ForEach-Object {
        $f = $_
        foreach ($pd in $protectDirs) { if ($f.FullName -like "$pd\*") { return } }
        if ($targetExt -notcontains $f.Extension.ToLower()) { return }
        if ($f.Name -eq 'cleanup.log') { return }
        if ($f.LastWriteTime -lt $cutoff) {
            $bytes += $f.Length
            Remove-Item $f.FullName -Force
            $count++
        }
    }
    return @($count, $bytes)
}

$total = @(0, 0)
$r = Clean-Dir 'C:\RockVideos\outbox'; $total[0]+=$r[0]; $total[1]+=$r[1]
$r = Clean-Dir 'C:\RockVideos\work';   $total[0]+=$r[0]; $total[1]+=$r[1]
$r = Clean-Dir 'C:\Scout\outbox';     $total[0]+=$r[0]; $total[1]+=$r[1]

$mb = [math]::Round($total[1] / 1MB, 1)
"$(Get-Date -Format 'yyyy-MM-dd HH:mm') cleaned $($total[0]) files, freed ${mb} MB" | Out-File $logFile -Append

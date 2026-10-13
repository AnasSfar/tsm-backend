# Extinction du PC apres le run YouTube planifie de 06:05 (2026-10-13).
#
# Appele par run_youtube.bat UNIQUEMENT si TSM_SHUTDOWN_AFTER=1, variable posee
# seulement dans l'action de la tache "TSM YouTube Videos Daily". Un lancement
# manuel du .bat n'eteint donc jamais rien, et une execution manuelle de la
# tache hors de la fenetre du matin (ex. 16h) non plus (garde horaire).
#
# Deroulement :
#   1. attend que les autres taches TSM en cours (ex. Apple Music 06:00) finissent ;
#   2. annule si une capture YouTube first-day/first-week est due dans les 24 h
#      (PC eteint = marque ratee = chiffre estime) ;
#   3. programme "shutdown /s /t 180" (filet : s'eteint meme si la fenetre plante)
#      et affiche une fenetre au premier plan avec compte a rebours :
#      "Annuler l'extinction" -> shutdown /a ; "Eteindre maintenant" -> /t 0.
#
# Test sans rien eteindre : powershell -File shutdown_after_run.ps1 -DryRun

param(
    [switch]$DryRun,
    [int]$WarnSeconds = 180,
    [int]$LatestHour = 9,          # pas d'extinction si le run finit apres 09:00
    [int]$MaxWaitMinutes = 60      # attente max des autres taches TSM
)

$ErrorActionPreference = 'Continue'
$log = Join-Path $PSScriptRoot 'run_youtube.log'
function Log($msg) {
    $line = " [shutdown] $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host $line
}

$now = Get-Date
if (-not $DryRun -and $now.Hour -ge $LatestHour) {
    Log "fin de run a $($now.ToString('HH:mm')) (apres ${LatestHour}h) : pas d'extinction"
    exit 0
}

# 2. Captures YouTube exactes a venir : ne pas les rater.
$horizon = $now.AddHours(24)
$pending = Get-ScheduledTask -TaskName 'TSM_YouTube_First*' -ErrorAction SilentlyContinue |
    Where-Object { $_.State -ne 'Disabled' } |
    ForEach-Object { $i = Get-ScheduledTaskInfo $_; if ($i.NextRunTime -and $i.NextRunTime -gt $now -and $i.NextRunTime -lt $horizon) { "$($_.TaskName) @ $($i.NextRunTime.ToString('dd/MM HH:mm'))" } }
if ($pending) {
    Log "capture YouTube prevue dans les 24 h ($($pending -join ', ')) : pas d'extinction"
    exit 0
}

# 1. Autres taches TSM encore en cours (la tache YouTube elle-meme exclue).
$deadline = $now.AddMinutes($MaxWaitMinutes)
while ($true) {
    $running = Get-ScheduledTask | Where-Object {
        $_.TaskName -match '^TSM' -and $_.TaskName -ne 'TSM YouTube Videos Daily' -and $_.State -eq 'Running'
    } | ForEach-Object { $_.TaskName }
    if (-not $running) { break }
    if ((Get-Date) -gt $deadline) {
        Log "taches encore en cours apres $MaxWaitMinutes min ($($running -join ', ')) : pas d'extinction"
        exit 0
    }
    Log "attente de : $($running -join ', ')"
    Start-Sleep -Seconds 30
}

if ($DryRun) {
    Log "DRY RUN : fenetre affichee, aucune extinction programmee"
} else {
    Log "extinction programmee dans $WarnSeconds s"
    & shutdown.exe /s /t $WarnSeconds /c "TSM : run YouTube termine, le PC s'eteint dans $([math]::Round($WarnSeconds / 60)) min." | Out-Null
}

# 3. Fenetre d'avertissement.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$eAcute = [char]0x00E9
$end = (Get-Date).AddSeconds($WarnSeconds)

$form = New-Object System.Windows.Forms.Form
$form.Text = "TSM - extinction du PC"
$form.Size = New-Object System.Drawing.Size(460, 210)
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false

$label = New-Object System.Windows.Forms.Label
$label.Location = New-Object System.Drawing.Point(20, 20)
$label.Size = New-Object System.Drawing.Size(410, 70)
$label.Font = New-Object System.Drawing.Font('Segoe UI', 12)
$form.Controls.Add($label)

$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = "Annuler l'extinction"
$cancel.Location = New-Object System.Drawing.Point(20, 105)
$cancel.Size = New-Object System.Drawing.Size(200, 45)
$cancel.Font = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancel)
$form.CancelButton = $cancel

$now_btn = New-Object System.Windows.Forms.Button
$now_btn.Text = "$([char]0x00C9)teindre maintenant"
$now_btn.Location = New-Object System.Drawing.Point(230, 105)
$now_btn.Size = New-Object System.Drawing.Size(200, 45)
$now_btn.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$now_btn.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($now_btn)

$update = {
    $left = [math]::Max(0, [int]($end - (Get-Date)).TotalSeconds)
    $label.Text = "Run YouTube termin$eAcute.`nLe PC s'$($eAcute)teint dans {0}:{1:00}." -f [math]::Floor($left / 60), ($left % 60)
    if ($left -le 0) { $form.DialogResult = [System.Windows.Forms.DialogResult]::Abort; $form.Close() }
}
& $update
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 1000
$timer.Add_Tick($update)
$timer.Start()
$form.Add_Shown({ $form.Activate() })

$result = $form.ShowDialog()
$timer.Stop()

if ($result -eq [System.Windows.Forms.DialogResult]::Cancel) {
    if (-not $DryRun) { & shutdown.exe /a | Out-Null }
    Log "extinction annulee par l'utilisateur"
} elseif ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    Log "extinction immediate demandee"
    if (-not $DryRun) { & shutdown.exe /s /t 0 | Out-Null }
} else {
    Log "pas de reponse : le shutdown programme suit son cours"
}
exit 0

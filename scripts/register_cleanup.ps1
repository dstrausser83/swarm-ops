# Register the Rock Cleanup task (hidden, S4U, daily 3am)
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\RockVideos\work\RockCleanup.ps1'
$trigger = New-ScheduledTaskTrigger -Daily -At 3am
$principal = New-ScheduledTaskPrincipal -UserId '<windows-username>' -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -Hidden
Register-ScheduledTask -TaskName 'Rock Cleanup' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
$t = Get-ScheduledTask -TaskName 'Rock Cleanup'
'Task: ' + $t.TaskName + ' Hidden=' + $t.Settings.Hidden + ' LogonType=' + $t.Principal.LogonType

# Records session state every run.
# - locked_sessions: session IDs whose lock/login screen (logonui) is present
# - svc_session: session(s) where xianyu-auto-reply python services run
# - lockscreen: whether the SERVICE session is locked (the metric that matters;
#   previously this was a global logonui check that false-positived from any session)
$ErrorActionPreference = 'SilentlyContinue'

$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$lockedSessions = @(Get-Process logonui | Select-Object -ExpandProperty SessionId -Unique)
$lockedList = if ($lockedSessions) { ($lockedSessions | Sort-Object) -join ',' } else { '-' }

$svcSessions = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.ExecutablePath -match 'xianyu-auto-reply' } |
    Select-Object -ExpandProperty SessionId -Unique)
$svcList = if ($svcSessions) { ($svcSessions | Sort-Object) -join ',' } else { '-' }

$svcLocked = 'unknown'
if ($svcSessions.Count -gt 0) {
    $hit = $svcSessions | Where-Object { $lockedSessions -contains $_ }
    $svcLocked = if ($hit) { 'True' } else { 'False' }
}

$rdp = [bool](Get-Process mstsc)
$line = quser 2>$null | Where-Object { $_ -match 'console|rdp-tcp' } | ForEach-Object { ($_ -replace '\s+', ' ').Trim() }

$log = 'C:\Users\Administrator\Desktop\code\xianyu\xianyu-auto-reply\session-state.log'
Add-Content -Path $log -Value ("$ts | lockscreen=$svcLocked | locked_sessions=$lockedList | svc_session=$svcList | mstsc=$rdp | $line")

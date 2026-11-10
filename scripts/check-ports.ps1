foreach ($p in 8089,8090,8091,9000,3306,6379) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) {
        $proc = (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        Write-Output "$p LISTENING (PID $($c.OwningProcess) $proc)"
    } else {
        Write-Output "$p free"
    }
}

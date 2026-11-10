foreach ($p in 8089,8090,8091) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$p/health" -TimeoutSec 3 -UseBasicParsing
        Write-Output "$p health: $($resp.StatusCode) $($resp.Content)"
    } catch {
        Write-Output "$p health: FAIL $($_.Exception.Message)"
    }
}

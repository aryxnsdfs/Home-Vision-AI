Write-Host "Auto-push script started. Checking for changes every 60 seconds..."
while ($true) {
    Start-Sleep -Seconds 60
    $status = git status --porcelain
    if ($status) {
        $time = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Host "Changes detected at $time, committing and pushing..."
        git add .
        git commit -m "Auto-commit: $time"
        git push origin main
    }
}

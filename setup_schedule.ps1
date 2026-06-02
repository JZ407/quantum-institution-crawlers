$action = New-ScheduledTaskAction -Execute 'C:\Python314\python.exe' -Argument '-X utf8 D:\Claude_code\institution_news\run_all.py'
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Wednesday,Friday -At '13:00'
Register-ScheduledTask -TaskName 'QTC_InstCrawl' -Action $action -Trigger $trigger -Force
Write-Host "Task 'QTC_InstCrawl' created successfully"

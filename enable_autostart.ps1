# Trae 每日签到机器人 - 开机自启注册工具
# 作用: 注册一个 Windows 计划任务, 登录时自动启动签到机器人
# 取消: Unregister-ScheduledTask -TaskName 'TraeCheckinBot' -Confirm:$false
$ErrorActionPreference = 'Stop'
$TaskName = 'TraeCheckinBot'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$Action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ProjectDir\start_bot.ps1`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null

Write-Host "已注册开机自启计划任务: $TaskName"
Write-Host '以后每次登录 Windows, 签到机器人都会自动启动(隐藏窗口运行, 日志在 logs 目录).'
Write-Host "如需取消, 请运行: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Read-Host '按回车退出'

#Requires -Version 5.1
<#
.SYNOPSIS
    把面板装成"开机自启 + 静默后台运行"，并清掉旧程序的自启。

.DESCRIPTION
    背景：旧程序 Install-LanMonitor.ps1 是用【SYSTEM 身份 + 开机触发 + 每 5 分钟看门狗】
    注册计划任务的，所以每次重启它都会抢回 8787。要赢过它，本面板也必须做成自启任务，
    但身份必须换掉，原因有两条硬约束：

      1) 必须跑在【交互式桌面会话】里 —— 截屏（mss / ImageGrab）依赖桌面会话，
         SYSTEM 的会话 0 抓不到真实屏幕，只会拿到黑屏。
      2) 必须是【最低权限】 —— 这个面板能在网页上执行命令，
         如果以管理员权限运行，等于把管理员级任意命令暴露给任何拿到令牌的人。

    所以本脚本注册的任务是：当前用户 + InteractiveToken + LeastPrivilege，
    用 pythonw.exe 启动（无控制台窗口，真正静默），
    触发方式为"登录时 + 每 5 分钟兜底"（已在运行时自动忽略新实例），
    并开启失败自动重启、无执行时间上限。

    本脚本只做安装，不碰面板代码本身。所有动作都会先打印再执行，并写日志。
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'sysadmin-panel',
    [int]    $Port     = 8787,
    [switch] $KeepOldTasks
)

$ErrorActionPreference = 'Continue'

function Write-Step { param([string]$Text) Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "    [OK] $Text" -ForegroundColor Green }
function Write-Note { param([string]$Text) Write-Host "    [!]  $Text" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 前置检查
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '必须提权运行。请双击 install-autostart.cmd。'
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir    = Join-Path $ScriptDir 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Transcript = Join-Path $LogDir ("install-autostart-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".log")
try { Start-Transcript -Path $Transcript -Force | Out-Null } catch { }

Write-Host '==============================================' -ForegroundColor White
Write-Host '  安装为开机自启的静默后台服务' -ForegroundColor White
Write-Host '==============================================' -ForegroundColor White
Write-Host "  执行身份: $($identity.Name)（已提权）"
Write-Host "  项目目录: $ScriptDir"

# ---------------------------------------------------------------- 找 pythonw
Write-Step '1/5 定位 pythonw.exe（无控制台窗口的关键）'

$pythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonExe) { $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $pythonExe) { throw 'PATH 里找不到 python，请先确认 Python 安装。' }

$pythonw = Join-Path (Split-Path $pythonExe) 'pythonw.exe'
if (-not (Test-Path $pythonw)) {
    Write-Note "找不到 $pythonw，退回 python.exe（会有控制台窗口，不再是静默运行）"
    $pythonw = $pythonExe
} else {
    Write-Ok "pythonw.exe = $pythonw"
}

$userId = "$env:USERDOMAIN\$env:USERNAME"
Write-Ok "任务身份 = $userId（交互式令牌 + 最低权限）"

# ---------------------------------------------------------------- 清旧自启
Write-Step '2/5 查找并移除旧程序（LanMonitor）的自启任务'

$oldTasks = @()
try {
    foreach ($task in (Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        $text = ($task.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments) $($_.WorkingDirectory)" }) -join ' '
        if ($text -match '(?i)LanMonitor|study-guard|8787') {
            $oldTasks += $task
        }
    }
} catch {
    Write-Note "枚举计划任务出错：$($_.Exception.Message)"
}

# 按名字再兜一次（有些任务在根目录且权限受限，全量枚举时读不到动作）
try {
    $byName = Get-ScheduledTask -TaskName 'LanMonitor' -ErrorAction SilentlyContinue
    if ($byName) { $oldTasks += $byName }
} catch { }

# 再用 schtasks 兜一次（它能列出部分 Get-ScheduledTask 读不到的任务）
foreach ($name in @('LanMonitor', '\LanMonitor')) {
    $out = & schtasks.exe /query /tn $name /fo LIST 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Note "schtasks 发现了任务：$name"
        try { $oldTasks += (Get-ScheduledTask -TaskName ($name -replace '^\\', '') -ErrorAction Stop) } catch { }
    }
}

$oldTasks = @($oldTasks | Where-Object { $_ } | Sort-Object -Property TaskName -Unique)

if (-not $oldTasks.Count) {
    Write-Note '没有找到旧程序的计划任务。若重启后旧程序仍会回来，说明它由别处拉起。'
} else {
    foreach ($task in $oldTasks) {
        $full = "$($task.TaskPath)$($task.TaskName)"
        Write-Host "    发现任务: $full   状态=$($task.State)  身份=$($task.Principal.UserId)"

        if ($KeepOldTasks) {
            Write-Note "  -KeepOldTasks 已指定，只禁用不删除。"
            try {
                Disable-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction Stop | Out-Null
                Write-Ok '  已禁用（可随时重新启用）'
            } catch { Write-Note "  禁用失败：$($_.Exception.Message)" }
            continue
        }

        # 先把任务 XML 备份出来，删错了也能导回去
        try {
            $xml = Export-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction Stop
            $backup = Join-Path $LogDir ("removed-task-" + ($task.TaskName -replace '[^\w\-]', '_') + ".xml")
            Set-Content -Path $backup -Value $xml -Encoding UTF8
            Write-Ok "  已备份任务定义 -> $backup"
        } catch {
            Write-Note "  备份任务定义失败：$($_.Exception.Message)"
        }

        try {
            Unregister-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -Confirm:$false -ErrorAction Stop
            Write-Ok "  已删除任务 $full"
        } catch {
            Write-Note "  删除失败：$($_.Exception.Message)"
        }
    }
}

# ---------------------------------------------------------------- 结束占用者
Write-Step "3/5 结束当前占用 $Port 的进程"

$conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if (-not $conns.Count) {
    Write-Ok "$Port 当前空闲。"
} else {
    foreach ($conn in $conns) {
        $procId = $conn.OwningProcess
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        $name = if ($proc) { $proc.ProcessName } else { '<未知>' }

        Write-Host "    占用者: PID=$procId  $name  $($conn.LocalAddress):$($conn.LocalPort)"
        if ($proc) {
            try {
                $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction Stop).CommandLine
                Write-Host "    命令行: $cmdline"
            } catch { }
        }

        # 保险：绝不动 DSH 本体
        if ($name -eq 'node' -and $Port -eq 3080) {
            Write-Note '这是 DSH 本体，脚本拒绝结束它。'
            continue
        }

        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Start-Sleep -Milliseconds 700
            if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
                Write-Note "PID $procId 仍然存在。"
            } else {
                Write-Ok "PID $procId 已结束。"
            }
        } catch {
            Write-Note "结束失败：$($_.Exception.Message)"
        }
    }
}

# ---------------------------------------------------------------- 注册任务
Write-Step "4/5 注册计划任务 $TaskName"

$now = Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>sysadmin-panel: 本机系统管理面板，静默后台运行（登录自启 + 每 5 分钟兜底 + 失败重试）</Description>
    <Author>$userId</Author>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>$userId</UserId>
    </LogonTrigger>
    <TimeTrigger>
      <Enabled>true</Enabled>
      <StartBoundary>$now</StartBoundary>
      <Repetition>
        <Interval>PT5M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$userId</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$pythonw</Command>
      <Arguments>run.py</Arguments>
      <WorkingDirectory>$ScriptDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

try {
    Register-ScheduledTask -TaskName $TaskName -Xml $xml -Force -ErrorAction Stop | Out-Null
    Write-Ok "任务已注册：$TaskName"
} catch {
    Write-Note "注册失败：$($_.Exception.Message)"
    Write-Note '若提示无效的 XML，可尝试把脚本另存为 UTF-8 BOM 后重跑。'
}

# ---------------------------------------------------------------- 启动验证
Write-Step '5/5 立即启动并验证'

try {
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Ok '已触发任务。'
} catch {
    Write-Note "触发失败：$($_.Exception.Message)"
}

Write-Host '    等待服务起来（最多 20 秒）...'
$ok = $false
for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Seconds 2
    $listen = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listen.Count) { $ok = $true; break }
}

if ($ok) {
    $owner = $listen[0].OwningProcess
    $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
    Write-Ok "$Port 已在监听：PID=$owner  $($proc.ProcessName)  地址=$($listen[0].LocalAddress)"
    if ($proc.ProcessName -eq 'pythonw') {
        Write-Ok '由 pythonw 启动 —— 无控制台窗口，静默运行。'
    }
} else {
    Write-Note "$Port 仍未监听。看日志："
    $serverLog = Join-Path $LogDir 'server.log'
    if (Test-Path $serverLog) {
        Get-Content $serverLog -Tail 20 -Encoding UTF8 | ForEach-Object { Write-Host "      $_" -ForegroundColor Gray }
    } else {
        Write-Host '      （还没有 server.log）' -ForegroundColor Gray
    }
}

# 用配置里的令牌做一次真实请求
$cfgPath = Join-Path $ScriptDir 'config.json'
if (Test-Path $cfgPath) {
    try {
        $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $headers = @{ 'X-Auth-Token' = $cfg.access_token }
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/ping" -Headers $headers -TimeoutSec 10
        Write-Ok "接口自测通过：$($resp | ConvertTo-Json -Compress)"
    } catch {
        Write-Note "接口自测失败：$($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------- 汇总
Write-Step '完成'
Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
    Select-Object TaskName, State,
        @{n='身份';e={$_.Principal.UserId}},
        @{n='登录方式';e={$_.Principal.LogonType}},
        @{n='权限';e={$_.Principal.RunLevel}} | Format-List

Write-Host '  常用操作（都需要管理员权限）:' -ForegroundColor Gray
Write-Host "    查看状态 : Get-ScheduledTask -TaskName $TaskName | Select TaskName,State" -ForegroundColor DarkGray
Write-Host "    停止     : Stop-ScheduledTask -TaskName $TaskName" -ForegroundColor DarkGray
Write-Host "    手动启动 : Start-ScheduledTask -TaskName $TaskName" -ForegroundColor DarkGray
Write-Host "    彻底卸载 : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false" -ForegroundColor DarkGray
Write-Host ''
Write-Host "  运行日志: $LogDir\server.log" -ForegroundColor Gray
Write-Host "  本次日志: $Transcript" -ForegroundColor Gray
try { Stop-Transcript | Out-Null } catch { }

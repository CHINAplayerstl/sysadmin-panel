#Requires -Version 5.1
<#
.SYNOPSIS
    把面板装成"任何人都能用、没人登录也在跑"的常驻服务。

.DESCRIPTION
    为什么这次要改成 SYSTEM 身份：

      旧方案的任务身份是 Firefly + 交互式令牌，所以
        - Firefly 不登录 -> 面板根本不启动
        - 换成 study 登录 -> 任务不会跑（Firefly 不在会话里）
      要做到"不管谁登录、甚至没人登录都能访问"，主服务必须以 SYSTEM 常驻。

    但 SYSTEM 跑在会话 0，而**截屏和剪贴板是会话级资源**，会话 0 只有黑屏。
    所以配套加了"会话内助手"（agent.py）：
      主服务用 WTS API（WTSQueryUserToken + CreateProcessAsUser）
      把助手送进当前活动会话，由助手在用户桌面上抓屏 / 读剪贴板再推回来。
      没人登录时，屏幕卡片会显示一张写清楚原因的占位图，而不是裂图。

    本脚本同时顺手修掉一件遗留小事：
      两个 Startup 文件夹里的 desktop.ini 丢了 Hidden+System 属性，
      导致登录时资源管理器把它们当普通启动项"打开"，弹出两个记事本。

    ⚠️ 安全提醒：面板以 SYSTEM 运行时，"命令执行"接口等于 SYSTEM 级任意命令。
       默认仍是白名单模式，但访问令牌就是唯一防线，务必用长随机串。
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'sysadmin-panel',
    [int]    $Port     = 8787
)

$ErrorActionPreference = 'Continue'

function Write-Step { param([string]$Text) Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "    [OK] $Text" -ForegroundColor Green }
function Write-Note { param([string]$Text) Write-Host "    [!]  $Text" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 前置检查
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '必须提权运行。请双击 install-service.cmd。'
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir    = Join-Path $ScriptDir 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Transcript = Join-Path $LogDir ("install-service-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".log")
try { Start-Transcript -Path $Transcript -Force | Out-Null } catch { }

Write-Host '==============================================' -ForegroundColor White
Write-Host '  安装为 SYSTEM 常驻服务（任何人登录都可用）' -ForegroundColor White
Write-Host '==============================================' -ForegroundColor White
Write-Host "  执行身份: $($identity.Name)（已提权）"
Write-Host "  项目目录: $ScriptDir"

# ---------------------------------------------------------------- 找 pythonw
Write-Step '1/6 定位 pythonw.exe'

$pythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonExe) { $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $pythonExe) { throw 'PATH 里找不到 python。' }

$pythonw = Join-Path (Split-Path $pythonExe) 'pythonw.exe'
if (-not (Test-Path $pythonw)) {
    Write-Note "找不到 pythonw.exe，退回 python.exe（会出现控制台窗口）"
    $pythonw = $pythonExe
} else {
    Write-Ok "pythonw.exe = $pythonw"
}

# ---------------------------------------------------------------- 清旧任务
Write-Step "2/6 移除旧的 $TaskName 任务（旧的以 Firefly 身份运行，只在他登录时可用）"

$old = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($old) {
    try {
        $xmlBackup = Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $backupPath = Join-Path $LogDir ("removed-task-" + $TaskName + "-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".xml")
        Set-Content -Path $backupPath -Value $xmlBackup -Encoding UTF8
        Write-Ok "旧任务定义已备份 -> $backupPath"
    } catch {
        Write-Note "备份旧任务定义失败：$($_.Exception.Message)"
    }

    # 先停掉它正在跑的实例，否则占用 8787
    try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
    Start-Sleep -Milliseconds 500

    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
        Write-Ok "旧任务已删除。"
    } catch {
        Write-Note "删除旧任务失败：$($_.Exception.Message)"
    }
} else {
    Write-Note '没有找到同名旧任务，跳过。'
}

# ---------------------------------------------------------------- 结束占用者
Write-Step "3/6 结束当前占用 $Port 的进程"

$conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if (-not $conns.Count) {
    Write-Ok "$Port 当前空闲。"
} else {
    foreach ($conn in $conns) {
        $procId = $conn.OwningProcess
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        $name = if ($proc) { $proc.ProcessName } else { '<未知>' }
        Write-Host "    占用者: PID=$procId  $name  $($conn.LocalAddress):$($conn.LocalPort)"

        if ($name -eq 'node' -and $Port -eq 3080) {
            Write-Note '这是 DSH 本体，拒绝结束。'
            continue
        }

        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Start-Sleep -Milliseconds 700
            Write-Ok "PID $procId 已结束。"
        } catch {
            Write-Note "结束失败：$($_.Exception.Message)"
        }
    }
}

# 清理"孤儿"服务进程：命令行里带 run.py 的 python / pythonw 一律收掉。
# 背景：os.execv 重启会在 Windows 上留下重复绑定的套接字，
#       表现为端口"在监听"但连接全部超时，必须把相关进程清干净。
$leaked = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -match 'run\.py' })
foreach ($p in $leaked) {
    Write-Host "    清理残留服务进程 PID=$($p.ProcessId)"
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { }
}
if ($leaked.Count) {
    Start-Sleep -Seconds 2
    Write-Ok "已清理 $($leaked.Count) 个残留服务进程。"
}

# 真正验证端口可用：实际绑定一次，比看 netstat 可靠
$bindOk = $false
try {
    $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $Port)
    $probe.Start()
    $probe.Stop()
    $bindOk = $true
} catch {
    $bindOk = $false
}

if ($bindOk) {
    Write-Ok "$Port 可以正常绑定。"
} else {
    Write-Note "$Port 仍无法绑定（多半残留了孤儿套接字）。若随后启动失败，重启一次电脑即可彻底清掉。"
}

# ---------------------------------------------------------------- 修 desktop.ini
Write-Step '4/6 修复 Startup 文件夹里 desktop.ini 的属性（止住登录弹两个记事本）'

# 原理：登录时资源管理器会遍历 Startup 目录并"启动"其中的条目。
#       正常的 desktop.ini 带 Hidden+System，会被跳过；
#       属性一旦丢失，它就变成普通条目被 ShellExecute 打开，
#       而 .ini 的默认处理程序是 NOTEPAD.EXE —— 于是弹出记事本。
$targets = @(
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\desktop.ini'),
    'C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup\desktop.ini'
)

foreach ($file in $targets) {
    if (-not (Test-Path -LiteralPath $file)) { Write-Note "不存在，跳过：$file"; continue }
    try {
        $item = Get-Item -LiteralPath $file -Force -ErrorAction Stop
        $before = $item.Attributes
        $item.Attributes = $item.Attributes -bor [IO.FileAttributes]::Hidden -bor [IO.FileAttributes]::System
        $after = (Get-Item -LiteralPath $file -Force).Attributes
        Write-Ok "$([IO.Path]::GetFileName((Split-Path $file -Parent))) -> $file"
        Write-Host "        属性: $before  =>  $after" -ForegroundColor Gray
    } catch {
        Write-Note "设置属性失败：$file -> $($_.Exception.Message)"
    }
}

# 顺手把残留的记事本窗口关掉（仅限打开 desktop.ini 的那些）
$closed = 0
Get-Process -Name notepad -ErrorAction SilentlyContinue | ForEach-Object {
    $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
    if ($cmdline -like '*desktop.ini*') {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        $closed++
    }
}
Write-Ok "关闭了 $closed 个 desktop.ini 记事本窗口。"

# ---------------------------------------------------------------- 注册任务
Write-Step "5/6 注册 SYSTEM 常驻任务 $TaskName"

$now = Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>sysadmin-panel: 本机系统管理面板。SYSTEM 常驻，开机自启 + 每 1 分钟兜底；屏幕/剪贴板由会话内助手提供。</Description>
    <Author>SYSTEM</Author>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
    </BootTrigger>
    <TimeTrigger>
      <Enabled>true</Enabled>
      <StartBoundary>$now</StartBoundary>
      <Repetition>
        <Interval>PT1M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
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
    Write-Ok "任务已注册（SYSTEM · 开机自启 · 每 1 分钟兜底）"
} catch {
    Write-Note "注册失败：$($_.Exception.Message)"
}

# ---------------------------------------------------------------- 启动验证
Write-Step '6/6 启动并验证'

try {
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Ok '已触发任务。'
} catch {
    Write-Note "触发失败：$($_.Exception.Message)"
}

Write-Host '    等待服务起来（最多 30 秒）...'
$listen = $null
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    $c = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($c.Count) { $listen = $c[0]; break }
}

if ($listen) {
    $owner = Get-Process -Id $listen.OwningProcess -ErrorAction SilentlyContinue
    Write-Ok "$Port 已在监听：PID=$($listen.OwningProcess)  $($owner.ProcessName)  地址=$($listen.LocalAddress)"
    try {
        $procOwner = (Get-CimInstance Win32_Process -Filter "ProcessId=$($listen.OwningProcess)").GetOwner()
        Write-Ok "运行身份：$($procOwner.Domain)\$($procOwner.User)（应为 SYSTEM）"
    } catch { }
} else {
    Write-Note "$Port 仍未监听，看日志："
    $serverLog = Join-Path $LogDir 'server.log'
    if (Test-Path $serverLog) {
        Get-Content $serverLog -Tail 20 -Encoding UTF8 | ForEach-Object { Write-Host "      $_" -ForegroundColor Gray }
    }
}

$cfgPath = Join-Path $ScriptDir 'config.json'
if (Test-Path $cfgPath) {
    try {
        $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/ping" -Headers @{ 'X-Auth-Token' = $cfg.access_token } -TimeoutSec 10
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

Write-Host '  现在的行为:' -ForegroundColor Gray
Write-Host '    - 开机 30 秒后自动启动，不需要任何人登录' -ForegroundColor DarkGray
Write-Host '    - Firefly 或 study 谁登录都能用；没人登录也能用（屏幕/剪贴板除外）' -ForegroundColor DarkGray
Write-Host '    - 有人登录后，屏幕画面会在 1~2 秒内自动出现' -ForegroundColor DarkGray
Write-Host ''
Write-Host '  管理命令（需管理员权限）:' -ForegroundColor Gray
Write-Host "    Get-ScheduledTask -TaskName $TaskName | Select TaskName,State" -ForegroundColor DarkGray
Write-Host "    Stop-ScheduledTask / Start-ScheduledTask -TaskName $TaskName" -ForegroundColor DarkGray
Write-Host "    Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false" -ForegroundColor DarkGray
Write-Host ''
Write-Host '  ⚠️ 面板以 SYSTEM 运行，"命令执行"接口等于 SYSTEM 级任意命令。' -ForegroundColor Yellow
Write-Host '     默认仍是白名单模式；令牌是唯一防线，建议尽快换成长随机串。' -ForegroundColor Yellow
Write-Host ''
Write-Host "  服务日志: $LogDir\server.log" -ForegroundColor Gray
Write-Host "  助手日志: $LogDir\agent.log" -ForegroundColor Gray
Write-Host "  本次日志: $Transcript" -ForegroundColor Gray
try { Stop-Transcript | Out-Null } catch { }

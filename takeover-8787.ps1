#Requires -Version 5.1
<#
.SYNOPSIS
    接管 8787 端口：结束旧程序 + 放行局域网访问。

.DESCRIPTION
    做两件事，都需要管理员权限：

      1) 找出并结束当前监听目标端口的旧程序。
         结束前会先把它的 PID / 路径 / 命令行 / 启动时间全部打印出来，
         并顺手检查有没有计划任务会在重启后把它拉起来。

      2) 添加入站防火墙规则，让同一局域网内的设备能访问这个端口。
         规则限定 RemoteAddress = LocalSubnet —— 只有同一个子网（例如
         192.168.5.0/24）的设备能连，不会顺着路由器暴露到公网。
         当前网络被 Windows 归类为 Public，所以这条限定很重要。

    本脚本【不会】以管理员身份去启动面板本身。
    面板要按普通用户权限运行：否则网页上的"命令执行"就变成管理员级任意命令，
    再叠加一个弱令牌，风险会大得没必要。
#>
[CmdletBinding()]
param(
    [int]    $Port     = 8787,
    [string] $RuleName = "sysadmin-panel TCP $Port (LocalSubnet)"
)

$ErrorActionPreference = 'Continue'

function Write-Step { param([string]$Text) Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "    [OK] $Text" -ForegroundColor Green }
function Write-Note { param([string]$Text) Write-Host "    [!]  $Text" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 前置检查
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '必须提权运行。请双击 takeover-8787.cmd。'
}

# 提权窗口的输出在自动化这边读不到，所以全程留一份日志便于核对
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir    = Join-Path $ScriptDir 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Transcript = Join-Path $LogDir ("takeover-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".log")
try { Start-Transcript -Path $Transcript -Force | Out-Null } catch { }

Write-Host '==============================================' -ForegroundColor White
Write-Host "  接管 $Port 端口" -ForegroundColor White
Write-Host '==============================================' -ForegroundColor White
Write-Host "  执行身份: $($identity.Name)（已提权）"

# ---------------------------------------------------------------- 1) 找占用者
Write-Step "1/2 查找监听 $Port 的进程"

$conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)

if (-not $conns.Count) {
    Write-Note "当前没有进程在监听 $Port，跳过结束步骤。"
} else {
    foreach ($conn in $conns) {
        $procId = $conn.OwningProcess
        Write-Host "    监听地址: $($conn.LocalAddress):$($conn.LocalPort)   PID=$procId"

        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "    进程名  : $($proc.ProcessName)"
            Write-Host "    可执行  : $($proc.Path)"
            Write-Host "    启动时间: $($proc.StartTime)"
            try {
                $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction Stop).CommandLine
                Write-Host "    命令行  : $cmdline"
            } catch {
                Write-Host "    命令行  : <读取失败>"
            }

            # 保险：绝不动 DSH 本体（3080 上的 node）
            $isDsh = $proc.ProcessName -eq 'node' -and "$($proc.Path)" -match 'node'
            if ($isDsh) {
                Write-Note "占用者是 node.exe，出于安全考虑本脚本拒绝结束它。请手动确认。"
                continue
            }

            Write-Host ''
            Write-Host "    正在结束 PID $procId ..." -ForegroundColor Yellow
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
                Start-Sleep -Milliseconds 800
                if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
                    Write-Note "PID $procId 仍然存在，可能被守护进程拉起了。"
                } else {
                    Write-Ok "PID $procId 已结束。"
                }
            } catch {
                Write-Note "结束失败：$($_.Exception.Message)"
            }
        } else {
            Write-Note "查不到 PID $procId 的进程详情。"
        }
    }
}

# 端口是否真的空出来了
Start-Sleep -Milliseconds 500
$still = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($still.Count) {
    Write-Note "$Port 仍被占用，稍后启动面板会失败。"
} else {
    Write-Ok "$Port 已空闲。"
}

# ---------------------------------------------------------------- 复活检查
Write-Step '附加检查：有没有计划任务会在重启后把它拉起来'

$hits = @()
try {
    foreach ($task in Get-ScheduledTask -ErrorAction SilentlyContinue) {
        foreach ($action in $task.Actions) {
            $text = "$($action.Execute) $($action.Arguments)"
            if ($text -match "$Port" -or $text -match 'HttpListener') {
                $hits += [pscustomobject]@{ Task = $task.TaskName; Path = $task.TaskPath; Action = $text.Trim() }
            }
        }
    }
} catch {
    Write-Note "计划任务枚举失败：$($_.Exception.Message)"
}

if ($hits.Count) {
    Write-Note '以下计划任务可能重新拉起旧程序（本脚本不擅自删除，请你自己判断）：'
    $hits | ForEach-Object { Write-Host "      [$($_.Path)$($_.Task)]  $($_.Action)" -ForegroundColor Gray }
} else {
    Write-Ok '没有发现相关计划任务。若重启后旧程序又出现，多半是别处的自启项。'
}

# ---------------------------------------------------------------- 2) 防火墙
Write-Step "2/2 放行局域网访问 TCP $Port（限定本地子网）"

# 先清掉同名旧规则，保证幂等
$existing = @(Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue)
if ($existing.Count) {
    $existing | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    Write-Ok "已移除同名旧规则 $($existing.Count) 条。"
}

try {
    New-NetFirewallRule `
        -DisplayName $RuleName `
        -Description 'sysadmin-panel: 只允许同一子网访问，不向公网暴露' `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -RemoteAddress LocalSubnet `
        -Profile Any `
        -Enabled True `
        -ErrorAction Stop | Out-Null
    Write-Ok "已添加规则：TCP $Port，来源限定 LocalSubnet"
} catch {
    Write-Note "添加防火墙规则失败：$($_.Exception.Message)"
}

Write-Host ''
Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue |
    Select-Object DisplayName, Enabled, Direction, Action, Profile |
    Format-Table -AutoSize

# ---------------------------------------------------------------- 汇总
Write-Step '完成'
Write-Host '  本机局域网地址（别人访问用这个）:' -ForegroundColor Gray
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    ForEach-Object { Write-Host "    http://$($_.IPAddress):$Port" -ForegroundColor Gray }

Write-Host ''
Write-Host '  下一步：回到普通（非管理员）窗口启动面板' -ForegroundColor Gray
Write-Host '    cd <项目目录>' -ForegroundColor DarkGray
Write-Host '    python run.py' -ForegroundColor DarkGray
Write-Host ''
Write-Host '  注意：本脚本不会帮你以管理员身份启动面板 —— 那样网页上的命令执行' -ForegroundColor Yellow
Write-Host '        就等于管理员级任意命令，配合弱令牌风险过大。' -ForegroundColor Yellow

Write-Host ''
Write-Host "  日志: $Transcript" -ForegroundColor DarkGray
try { Stop-Transcript | Out-Null } catch { }

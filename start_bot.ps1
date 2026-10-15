# Trae 每日签到机器人 - 启动脚本
# 作用: 自动创建虚拟环境、安装依赖、校准模板(首次)、启动签到机器人
$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

Write-Host '============================================'
Write-Host ' Trae 每日签到机器人'
Write-Host '============================================'
Write-Host ''

# 1. 检查 Python
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host '[错误] 未找到 Python, 请先安装 Python 3.9+ (https://www.python.org/downloads/)' -ForegroundColor Red
    Read-Host '按回车退出'
    exit 1
}
Write-Host "[1/5] Python: $(& python --version 2>&1)"

# 2. 创建虚拟环境
$VenvPython = Join-Path $ProjectDir '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    Write-Host '[2/5] 首次运行, 正在创建虚拟环境(.venv)...'
    & python -m venv (Join-Path $ProjectDir '.venv')
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[错误] 虚拟环境创建失败' -ForegroundColor Red
        Read-Host '按回车退出'
        exit 1
    }
} else {
    Write-Host '[2/5] 虚拟环境已存在'
}

# 3. 安装依赖(首次或 requirements.txt 更新时)
$DepStamp = Join-Path $ProjectDir '.venv\.deps_installed'
$NeedInstall = $true
if ((Test-Path $DepStamp) -and (Test-Path (Join-Path $ProjectDir 'requirements.txt'))) {
    if ((Get-Item $DepStamp).LastWriteTime -gt (Get-Item (Join-Path $ProjectDir 'requirements.txt')).LastWriteTime) {
        $NeedInstall = $false
    }
}
if ($NeedInstall) {
    Write-Host '[3/5] 正在安装依赖(首次运行需要几分钟, 请耐心等待)...'
    & $VenvPython -m pip install --upgrade pip --quiet
    & $VenvPython -m pip install -r (Join-Path $ProjectDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[错误] 依赖安装失败, 请检查网络后重试' -ForegroundColor Red
        Read-Host '按回车退出'
        exit 1
    }
    New-Item -ItemType File -Path $DepStamp -Force | Out-Null
    Write-Host '[3/5] 依赖安装完成'
} else {
    Write-Host '[3/5] 依赖已安装'
}

# 4. 首次使用: 录制按钮模板
$TplOk = (Test-Path (Join-Path $ProjectDir 'templates\account_button.png')) -and
         (Test-Path (Join-Path $ProjectDir 'templates\checkin_button.png'))
if (-not $TplOk) {
    Write-Host '[4/5] 尚未录制按钮模板, 启动校准工具(按提示框选按钮)...'
    & $VenvPython (Join-Path $ProjectDir 'capture_templates.py')
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[错误] 校准未完成' -ForegroundColor Red
        Read-Host '按回车退出'
        exit 1
    }
} else {
    Write-Host '[4/5] 按钮模板已就绪'
}

# 5. 启动签到机器人(每 24 小时自动签到)
Write-Host '[5/5] 签到机器人启动中...'
Write-Host ''
Write-Host '提示: 机器人会立即签到一次, 之后每 24 小时自动执行.'
Write-Host '      关闭本窗口即可停止; 紧急停止: 把鼠标移到屏幕左上角.'
Write-Host '      日志见 logs\checkin.log, 结果截图见 logs\screenshots\'
Write-Host ''
& $VenvPython (Join-Path $ProjectDir 'checkin_bot.py')
$BotExit = $LASTEXITCODE
Write-Host ''
Write-Host "签到机器人已退出 (退出码 $BotExit)"
Read-Host '按回车关闭窗口'
exit $BotExit

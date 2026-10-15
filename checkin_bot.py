#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trae 每日签到机器人
==================

工作流程:
  1. 找到并激活 Trae 窗口
  2. 点击左下角账号区域, 打开账号菜单弹窗
  3. 在弹窗中点击"签到"按钮
  4. 截图留证并关闭弹窗
  5. 按配置的时间间隔(默认 24 小时)自动循环执行

用法:
  python checkin_bot.py                 启动机器人(立即执行一次, 之后每 24 小时循环)
  python checkin_bot.py --once          只执行一次签到(测试用)
  python checkin_bot.py --no-initial-run 启动后不立即执行, 等到期再执行

注意:
  - 首次使用前必须先运行 capture_templates.py 录制按钮模板
  - 紧急停止: 把鼠标快速甩到屏幕左上角(pyautogui 防失控机制), 或按 Ctrl+C
"""

from __future__ import annotations

import argparse
import atexit
import collections
import ctypes
import datetime as dt
import json
import logging
import numpy as np
import os
import random
import sys
import time
import traceback
from ctypes import wintypes
from pathlib import Path
from PIL import Image

# ---------------------------------------------------------------------------
# Windows 高 DPI 适配: 必须在任何截图/鼠标操作之前设置,
# 否则在 125% / 150% 等显示缩放下, 坐标会错位.
# ---------------------------------------------------------------------------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

import pyautogui

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
LOG_DIR = BASE_DIR / "logs"
SHOT_DIR = LOG_DIR / "screenshots"
CONFIG_PATH = BASE_DIR / "config.json"
STATUS_PATH = LOG_DIR / "status.json"
PID_FILE = LOG_DIR / "bot.pid"
LEARNED_FILE = LOG_DIR / "learned_offsets.json"

DEFAULT_CONFIG = {
    "schedule_mode": "interval",        # interval=固定间隔 / daily=每天固定时间
    "interval_hours": 24,               # 每隔多少小时签到一次
    "daily_time": "09:00",              # schedule_mode=daily 时的每天执行时间
    "run_immediately_on_start": True,   # 启动时是否立即执行一次
    "window_keywords": ["Trae", "Agent"],  # 查找 Trae 窗口用的标题关键字(本机标题为 Agent)
    "process_keywords": ["trae"],          # 查找 Trae 窗口用的进程名关键字(优先, 不受标题影响)
    "restore_minimized_state": True,       # 签到结束后把最小化的 Trae 还原为最小化
    "confidence": 0.85,                 # 模板匹配置信度(0~1), 匹配不到可适当降低
    "grayscale": False,                 # 是否用灰度匹配(主题变色时更稳, 但误判风险略高)
    "popup_wait_seconds": 1.5,          # 点击账号后等待弹窗的秒数
    "result_wait_seconds": 2.5,         # 点击签到后等待结果并截图的秒数
    "retry_delay_minutes": 30,          # 失败后多少分钟重试
    "max_retries_per_cycle": 3,         # 每轮最多重试次数
    "account_offset": {"x": 110, "y": -38},   # 账号按钮兜底偏移(相对窗口左下角, 之后被自学习偏移取代)
    "match_scales": [0.8, 1.0, 1.25, 1.5],    # 模板匹配缩放倍数, 适配显示缩放(100%~150%)变化
    "random_delay_range": [0.4, 1.2],   # 每次点击前随机停顿, 模拟真人
}

CONFIG: dict = {}
LOG: logging.Logger | None = None
STATUS: dict = {}

# 与 pyscreeze.Box 兼容的命名元组(left, top, width, height), pyautogui.center() 可直接使用
Box = collections.namedtuple("Box", "left top width height")


class CheckinError(Exception):
    """可预期的签到失败(窗口找不到/按钮匹配不到等)."""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("checkin")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_DIR / "checkin.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"[警告] 配置文件读取失败, 使用默认配置: {exc}")
    return cfg


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def is_process_alive(pid: int) -> bool:
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


def ensure_single_instance() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            old_pid = None
        if old_pid and is_process_alive(old_pid):
            msg = f"机器人似乎已在运行 (PID {old_pid}). 如确认没有, 请删除 {PID_FILE} 后重试."
            if LOG:
                LOG.error(msg)
            else:
                print(msg)
            sys.exit(2)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(lambda: PID_FILE.exists() and PID_FILE.unlink())


def check_dependencies() -> None:
    try:
        import cv2  # noqa: F401
    except ImportError:
        msg = "缺少 opencv, 模板匹配不可用. 请先安装: pip install -r requirements.txt"
        if LOG:
            LOG.error(msg)
        else:
            print(msg)
        sys.exit(2)


def templates_ready() -> bool:
    return (TEMPLATE_DIR / "account_button.png").exists() and \
           (TEMPLATE_DIR / "checkin_button.png").exists()


def save_screenshot(tag: str) -> str:
    try:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SHOT_DIR / f"{tag}_{ts}.png"
        pyautogui.screenshot(str(path))
        return str(path)
    except Exception as exc:
        if LOG:
            LOG.warning(f"截图保存失败: {exc}")
        return "(截图失败)"


def write_status(result: str | None = None, detail: str = "", next_run: dt.datetime | None = None) -> None:
    if result is not None:
        STATUS["last_run"] = dt.datetime.now().isoformat(timespec="seconds")
        STATUS["result"] = result
        STATUS["detail"] = detail
    if next_run is not None:
        STATUS["next_run"] = next_run.isoformat(timespec="seconds")
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(STATUS, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_learned() -> dict:
    """读取自学习的按钮相对偏移(模板失配时的兜底位置)."""
    try:
        return json.loads(LEARNED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_learned(data: dict) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        LEARNED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 窗口操作
# ---------------------------------------------------------------------------

def _window_thread_id(hwnd: int) -> int:
    return ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)


def force_foreground_window(hwnd: int) -> bool:
    """把指定窗口切到前台(绕过 Windows 的前台窗口限制)."""
    user32 = ctypes.windll.user32
    if user32.GetForegroundWindow() == hwnd:
        return True
    cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_thread = _window_thread_id(user32.GetForegroundWindow())
    try:
        if fg_thread and fg_thread != cur_thread:
            user32.AttachThreadInput(fg_thread, cur_thread, True)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        if fg_thread and fg_thread != cur_thread:
            user32.AttachThreadInput(fg_thread, cur_thread, False)
    except Exception:
        pass
    return user32.GetForegroundWindow() == hwnd


def _process_image_name(hwnd: int) -> str:
    """查询窗口所属进程的可执行文件路径, 失败返回空串."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(handle)
    return ""


def _window_area(win) -> int:
    try:
        return max(0, int(win.width)) * max(0, int(win.height))
    except Exception:
        return 0


def _is_minimized(win) -> bool:
    try:
        return bool(win.isMinimized)
    except Exception:
        return False


def _find_window_by_hwnd(hwnd: int):
    import pygetwindow as gw

    try:
        for win in gw.getAllWindows():
            if getattr(win, "_hWnd", None) == hwnd:
                return win
    except Exception:
        pass
    return None


def find_trae_window():
    """查找 Trae 主窗口, 返回 (窗口对象, 之前是否处于最小化状态).

    Trae 的窗口标题是工作区名(本机为 'Agent'), 不一定含 'Trae' 字样,
    因此优先按进程名匹配(Trae CN.exe / Trae.exe); 进程名拿不到时,
    退化为按窗口标题精确匹配(config.json 的 window_keywords).

    最小化的窗口几何信息是离屏占位值, 会先还原再重新读取真实坐标.
    """
    import pygetwindow as gw

    proc_keywords = [str(k).lower() for k in CONFIG.get("process_keywords", ["trae"])]
    title_keywords = [str(k).lower() for k in CONFIG.get("window_keywords", ["Trae", "Agent"])]

    candidates = []
    try:
        windows = gw.getAllWindows()
    except Exception:
        windows = []

    for win in windows:
        hwnd = getattr(win, "_hWnd", None)
        if not hwnd:
            continue
        try:
            if not win.visible:
                continue
        except Exception:
            continue
        # 强匹配: 进程可执行文件名包含 trae(不受窗口标题影响)
        proc = _process_image_name(hwnd)
        if proc:
            exe = proc.rsplit("\\", 1)[-1].lower()
            if any(k in exe for k in proc_keywords):
                candidates.append(win)
                continue
        # 兜底: 窗口标题与关键字精确相等(避免误匹配 CodeBuddy 的 'Agents' 等)
        if win.title and win.title.strip().lower() in title_keywords:
            candidates.append(win)

    if not candidates:
        return None, False

    # 优先选未最小化的大窗口; 全部最小化时取面积最大的候选
    normal = [w for w in candidates if not _is_minimized(w) and _window_area(w) >= 90000]
    best = max(normal, key=_window_area) if normal else max(candidates, key=_window_area)
    was_minimized = _is_minimized(best)

    if was_minimized:
        # 最小化时 win.left/top/width/height 是离屏占位值, 先还原再重新读取
        try:
            best.restore()
            time.sleep(1.0)
        except Exception:
            pass
        refreshed = _find_window_by_hwnd(getattr(best, "_hWnd", 0))
        if refreshed is not None:
            best = refreshed
    return best, was_minimized


def activate_window(win) -> bool:
    hwnd = getattr(win, "_hWnd", None)
    try:
        if getattr(win, "isMinimized", False):
            win.restore()
            time.sleep(0.4)
    except Exception:
        pass
    ok = False
    if hwnd:
        ok = force_foreground_window(hwnd)
    if not ok:
        try:
            win.activate()
            time.sleep(0.3)
            ok = True
        except Exception:
            ok = False
    time.sleep(0.4)
    if hwnd:
        ok = ctypes.windll.user32.GetForegroundWindow() == hwnd
    if LOG:
        LOG.info(f"窗口切换前台: {'成功' if ok else '失败'} ({win.left},{win.top}) {win.width}x{win.height}")
    return ok


def screen_region(win):
    """把模板匹配范围限制在 Trae 窗口内(略外扩), 更快也更准."""
    try:
        sw, sh = pyautogui.size()
    except Exception:
        return None
    x1 = max(0, win.left - 20)
    y1 = max(0, win.top - 20)
    x2 = min(sw, win.left + win.width + 20)
    y2 = min(sh, win.top + win.height + 20)
    if x2 - x1 < 50 or y2 - y1 < 50:
        return None
    return (x1, y1, x2 - x1, y2 - y1)


# ---------------------------------------------------------------------------
# 模板匹配与点击
# ---------------------------------------------------------------------------

def _read_template(path: Path, grayscale: bool):
    """读取模板图片为 OpenCV 数组(BGR 或灰度)."""
    import cv2  # 延迟导入, 缺依赖时由 check_dependencies 给出友好提示

    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    arr = np.array(img)
    if grayscale:
        return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def find_template(name: str, region=None):
    """在屏幕上查找模板图片, 返回 Box 或 None.

    多尺度自适应匹配: 先按模板原始尺度匹配(快), 不中再按
    config.json 的 match_scales 逐个缩放匹配. 这样即使以后改了
    Windows 显示缩放(100%<->125%<->150%) 或 Trae 界面元素大小
    变化, 也依然能找得到. 窗口移动/缩放不影响匹配(匹配在窗口
    区域内进行, 与绝对位置无关).
    """
    import cv2  # 延迟导入, 缺依赖时由 check_dependencies 给出友好提示

    path = TEMPLATE_DIR / f"{name}.png"
    if not path.exists():
        return None
    grayscale = bool(CONFIG.get("grayscale", False))
    base_conf = float(CONFIG.get("confidence", 0.85))

    try:
        if region:
            shot = pyautogui.screenshot(region=region)
            ox, oy = int(region[0]), int(region[1])
        else:
            shot = pyautogui.screenshot()
            ox, oy = 0, 0
    except Exception as exc:
        LOG and LOG.warning(f"截图失败: {exc}")
        return None

    screen = np.array(shot)
    screen = cv2.cvtColor(screen, cv2.COLOR_RGB2GRAY if grayscale else cv2.COLOR_RGB2BGR)
    sh, sw = screen.shape[:2]

    tpl = _read_template(path, grayscale)
    th, tw = tpl.shape[:2]
    if tw > sw or th > sh:
        LOG and LOG.warning(f"模板 {name} 比匹配区域还大, 跳过")
        return None

    scales = [float(s) for s in CONFIG.get("match_scales", [0.8, 1.0, 1.25, 1.5])]
    if not any(abs(s - 1.0) < 1e-6 for s in scales):
        scales.append(1.0)  # 保证总是先试原始尺度
    ordered = [1.0] + sorted(s for s in set(scales) if abs(s - 1.0) > 1e-6)

    best = None  # (score, x, y, w, h)
    for scale in ordered:
        if abs(scale - 1.0) < 1e-6:
            t = tpl
        else:
            nw, nh = int(round(tw * scale)), int(round(th * scale))
            if nw < 1 or nh < 1 or nw > sw or nh > sh:
                continue
            interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
            t = cv2.resize(tpl, (nw, nh), interpolation=interp)
        res = cv2.matchTemplate(screen, t, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if best is None or max_val > best[0]:
            best = (float(max_val), max_loc[0], max_loc[1], t.shape[1], t.shape[0])
        # 原始尺度高置信度命中就不再试其他缩放(绝大多数情况走这条快路)
        if abs(scale - 1.0) < 1e-6 and max_val >= min(0.92, base_conf + 0.05):
            break

    if best is None:
        return None
    score, x, y, w, h = best
    if score >= base_conf:
        return Box(ox + x, oy + y, w, h)
    if score >= max(0.70, base_conf - 0.08):
        LOG and LOG.info(f"模板 {name} 以较低置信度 {score:.2f} 匹配(阈值 {base_conf:.2f})")
        return Box(ox + x, oy + y, w, h)
    LOG and LOG.info(f"模板 {name} 未匹配(最高置信度 {score:.2f})")
    return None


def _move_mouse_to_safe_spot(win) -> None:
    """把鼠标移到窗口中央的安全位置.

    如果鼠标恰好悬停在账号按钮上, 悬停高亮会改变按钮像素, 导致模板
    匹配失败. 匹配前先把鼠标挪到与按钮无关的区域.
    """
    try:
        pyautogui.moveTo(win.left + win.width // 2, win.top + win.height // 2, duration=0.25)
        time.sleep(0.3)
    except Exception:
        pass


def human_click(x: int, y: int) -> None:
    lo, hi = CONFIG.get("random_delay_range", [0.3, 1.0])
    time.sleep(random.uniform(float(lo), float(hi)))
    pyautogui.moveTo(x, y, duration=random.uniform(0.1, 0.3))
    pyautogui.click(x, y)


def _restore_minimized(win, was_minimized: bool) -> None:
    """签到结束后, 如果 Trae 之前是最小化状态, 就还回到最小化, 不打扰用户."""
    if not was_minimized or not CONFIG.get("restore_minimized_state", True):
        return
    try:
        win.minimize()
        LOG and LOG.info("已将 Trae 窗口还原为最小化状态")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 签到主流程
# ---------------------------------------------------------------------------

def do_checkin() -> str:
    """执行一次签到, 返回 'success' 或 'already'."""
    # 0) 先关掉可能残留的弹窗/对话框, 保证初始状态干净
    pyautogui.press("esc")
    time.sleep(0.3)

    # 1) 找到并激活 Trae 窗口
    win, was_minimized = find_trae_window()
    if win is None:
        raise CheckinError("未找到 Trae 窗口, 请确认 Trae 已经启动")
    if not activate_window(win):
        raise CheckinError("无法把 Trae 窗口切换到前台, 请检查是否有其他全屏程序遮挡")
    region = screen_region(win)
    learned = load_learned()
    win_bottom = win.top + win.height

    # 2) 点击左下角账号区域, 打开账号菜单
    _move_mouse_to_safe_spot(win)  # 先把鼠标挪开, 避免悬停高亮导致匹配失败
    box = find_template("account_button", region)
    if box:
        ax, ay = pyautogui.center(box)
        LOG and LOG.info(f"模板匹配到账号按钮 ({ax},{ay})")
        # 记录账号按钮相对窗口左下角的位置, 供模板失配时兜底(窗口移动/缩放后依然有效)
        learned["account_dx"] = int(ax - win.left)
        learned["account_dy"] = int(ay - win_bottom)
    else:
        dx = int(learned.get("account_dx", CONFIG["account_offset"]["x"]))
        dy = int(learned.get("account_dy", CONFIG["account_offset"]["y"]))
        ax, ay = win.left + dx, win_bottom + dy
        LOG and LOG.warning("未匹配到账号按钮模板, 使用相对窗口的自适应偏移兜底")
    human_click(ax, ay)

    # 3) 等待弹窗出现
    time.sleep(float(CONFIG["popup_wait_seconds"]))

    # 4) 已签到判断(可选模板 checked_in_button.png)
    if find_template("checked_in_button", region):
        LOG and LOG.info("检测到【已签到】状态, 今天无需重复签到")
        pyautogui.press("esc")
        save_learned(learned)
        _restore_minimized(win, was_minimized)
        return "already"

    # 5) 点击弹窗中的"签到"按钮
    box = find_template("checkin_button", region)
    if box:
        cx, cy = pyautogui.center(box)
        LOG and LOG.info(f"模板匹配到签到按钮 ({cx},{cy})")
        # 弹窗锚定在账号按钮上方, 记录其相对账号按钮的偏移供兜底
        learned["checkin_dx"] = int(cx - ax)
        learned["checkin_dy"] = int(cy - ay)
    elif "checkin_dx" in learned and "checkin_dy" in learned:
        cx = ax + int(learned["checkin_dx"])
        cy = ay + int(learned["checkin_dy"])
        LOG and LOG.warning(f"未匹配到签到按钮模板, 使用自学习的相对偏移兜底 ({cx},{cy})")
    else:
        shot = save_screenshot("checkin_button_not_found")
        pyautogui.press("esc")
        save_learned(learned)
        raise CheckinError(f"未找到签到按钮(可能界面已更新或今天已签到). 截图: {shot}")
    human_click(cx, cy)

    # 6) 等待结果并截图留证
    time.sleep(float(CONFIG["result_wait_seconds"]))
    shot = save_screenshot("after_checkin_click")
    LOG and LOG.info(f"已点击签到按钮, 结果截图: {shot}")

    # 7) 关闭弹窗
    pyautogui.press("esc")
    save_learned(learned)
    _restore_minimized(win, was_minimized)
    return "success"


def run_once() -> bool:
    started = dt.datetime.now()
    try:
        state = do_checkin()
        if state == "already":
            write_status(result="already", detail="今天已经签到过")
            return True
        write_status(result="success", detail="签到成功")
        return True
    except CheckinError as exc:
        LOG and LOG.error(f"签到失败: {exc}")
        write_status(result="failed", detail=str(exc))
        return False
    except Exception as exc:
        LOG and LOG.error(f"发生未预期的错误: {exc}\n{traceback.format_exc()}")
        shot = save_screenshot("error")
        write_status(result="failed", detail=f"{exc} (截图: {shot})")
        return False
    finally:
        LOG and LOG.info(f"本次执行耗时 { (dt.datetime.now() - started).total_seconds():.1f} 秒")


def run_with_retry() -> bool:
    max_retries = int(CONFIG.get("max_retries_per_cycle", 3))
    delay_minutes = float(CONFIG.get("retry_delay_minutes", 30))
    for attempt in range(1, max_retries + 1):
        LOG and LOG.info(f"----- 第 {attempt}/{max_retries} 次尝试 -----")
        if run_once():
            return True
        if attempt < max_retries:
            LOG and LOG.info(f"{delay_minutes:.0f} 分钟后重试...")
            time.sleep(delay_minutes * 60)
    return False


def next_run_time() -> dt.datetime:
    mode = str(CONFIG.get("schedule_mode", "interval"))
    now = dt.datetime.now()
    if mode == "daily":
        try:
            hh, mm = str(CONFIG.get("daily_time", "09:00")).split(":")
            nxt = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            nxt = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += dt.timedelta(days=1)
        return nxt
    return now + dt.timedelta(hours=float(CONFIG.get("interval_hours", 24)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trae 每日签到机器人")
    parser.add_argument("--once", action="store_true", help="只执行一次签到(测试用)")
    parser.add_argument("--no-initial-run", action="store_true", help="启动后不立即执行, 等到期再执行")
    return parser.parse_args()


def main() -> int:
    global CONFIG, LOG
    args = parse_args()
    CONFIG = load_config()
    LOG = setup_logging()
    ensure_single_instance()
    check_dependencies()

    if not templates_ready():
        LOG.error("缺少按钮模板, 请先运行校准工具:  python capture_templates.py")
        return 2

    pyautogui.FAILSAFE = True   # 鼠标甩到左上角可紧急中止
    pyautogui.PAUSE = 0.1

    if args.once:
        return 0 if run_with_retry() else 1

    LOG.info("Trae 签到机器人已启动 (Ctrl+C 退出; 紧急停止: 把鼠标移到屏幕左上角)")
    if CONFIG.get("run_immediately_on_start", True) and not args.no_initial_run:
        run_with_retry()

    while True:
        nxt = next_run_time()
        write_status(next_run=nxt)
        LOG.info(f"下次签到时间: {nxt:%Y-%m-%d %H:%M:%S}")
        while True:
            remaining = (nxt - dt.datetime.now()).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(30.0, max(1.0, remaining)))
        run_with_retry()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已手动停止")
        sys.exit(0)

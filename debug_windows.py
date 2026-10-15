#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断工具: 列出当前所有可见窗口的标题/尺寸, 用于确定 Trae 窗口的识别关键字."""
import pygetwindow as gw


def safe(s: str) -> str:
    return s.encode("unicode_escape").decode("ascii")


lines = [f"{'标题(转义后)':<55} {'尺寸':<12} hwnd", "-" * 90]
for win in gw.getAllWindows():
    try:
        if not win.visible or not win.title:
            continue
        hwnd = getattr(win, "_hWnd", 0)
        lines.append(f"{safe(win.title)[:53]:<55} {win.width}x{win.height:<7} {hwnd}")
    except Exception as exc:
        lines.append(f"(读取失败: {exc})")
lines.append("-" * 90)
lines.append("说明: \\u200b 是零宽空格, \\uXXXX 是中文/特殊字符, 不影响关键字匹配")
out = "\n".join(lines)
print(out)
with open("debug_windows.txt", "w", encoding="utf-8") as f:
    f.write(out)

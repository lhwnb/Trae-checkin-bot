#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trae 签到机器人 - 按钮模板校准工具
==================================

每个人的屏幕分辨率/缩放比例/主题都不同, 所以需要先录制两个按钮的截图模板:
  1. checkin_button.png   账号弹窗里的"签到"按钮
  2. account_button.png   程序左下角的账号区域

录制方法:
  工具会先截取整个屏幕(冻结画面), 然后用鼠标在截图上拖动框选目标按钮,
  松开鼠标即保存. 弹窗不会因为失焦而关闭, 可以放心框选.

用法:
  python capture_templates.py
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from PIL import Image, ImageGrab, ImageTk

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

MIN_SIZE = 6  # 框选区域小于该像素数视为误操作


class RegionSelector:
    """全屏显示一张截图, 允许用户拖动框选区域, 返回原图像素坐标下的 bbox."""

    def __init__(self, image: Image.Image, title: str, hint: str):
        self.image = image
        self.bbox = None
        self._start = None
        self._rect = None

        self.root = tk.Tk()
        self.root.title(title)
        self.root.attributes("-fullscreen", True)
        self.root.configure(bg="black")
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.focus_force()

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        iw, ih = image.size
        scale = min(sw / iw, sh / ih, 1.0)  # 只在多屏(截图大于单屏)时缩小
        dw, dh = max(1, int(iw * scale)), max(1, int(ih * scale))
        if scale != 1.0:
            disp = image.resize((dw, dh), Image.Resampling.LANCZOS)
        else:
            disp = image
        self.scale = scale
        self.tk_image = ImageTk.PhotoImage(disp)

        self.canvas = tk.Canvas(self.root, width=dw, height=dh,
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack(expand=True)
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)
        self.canvas.create_text(
            16, 12, anchor="nw", fill="#ffdd57",
            font=("Microsoft YaHei", 15),
            text=f"{hint}  |  按住左键拖动框选, 松开保存; Esc 取消",
        )
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

    def _on_press(self, event):
        self._start = (event.x, event.y)
        if self._rect:
            self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#ff4d4f", width=2)

    def _on_drag(self, event):
        if self._start and self._rect:
            self.canvas.coords(self._rect, self._start[0], self._start[1], event.x, event.y)

    def _on_release(self, event):
        if not self._start:
            return
        x1, y1 = self._start
        x2, y2 = event.x, event.y
        self._start = None
        if abs(x2 - x1) < MIN_SIZE or abs(y2 - y1) < MIN_SIZE:
            return
        bx1 = int(min(x1, x2) / self.scale)
        by1 = int(min(y1, y2) / self.scale)
        bx2 = int(max(x1, x2) / self.scale)
        by2 = int(max(y1, y2) / self.scale)
        self.bbox = (bx1, by1, bx2, by2)
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.bbox


def capture(tag: str, title: str, hint: str) -> bool:
    """提示 -> 截屏 -> 框选 -> 保存模板. 用户取消时可选择重试."""
    print(f"\n>>> {hint}")
    while True:
        input("    准备好后按回车键, 工具将截取当前屏幕 ...")
        image = ImageGrab.grab()
        print("    已截取屏幕, 请在弹出窗口中用鼠标拖动框选目标 ...")
        selector = RegionSelector(image, title, hint)
        bbox = selector.run()
        if bbox is None:
            again = input("    已取消. 重新选择? (y=重试 / n=跳过): ").strip().lower()
            if again == "y":
                continue
            return False
        crop = image.crop(bbox)
        out = TEMPLATE_DIR / f"{tag}.png"
        crop.save(out)
        print(f"    已保存模板: {out}  (大小 {crop.size[0]}x{crop.size[1]})")
        return True


def main():
    print("=" * 60)
    print(" Trae 签到机器人 - 按钮模板校准工具")
    print("=" * 60)

    print("\n[第 1 步] 录制【签到】按钮")
    print("  请先在 Trae 中点击左下角账号, 打开签到弹窗, 并保持弹窗打开.")
    ok1 = capture("checkin_button", "录制签到按钮",
                  "请框选账号弹窗中的【签到】按钮")

    print("\n[第 2 步] 录制【账号】按钮")
    print("  请先关闭刚才的弹窗(按 Esc 或点击空白处), 保持 Trae 主界面.")
    ok2 = capture("account_button", "录制账号按钮",
                  "请框选程序左下角的账号区域(用户名/头像)")

    print("\n[第 3 步|可选] 录制【已签到】按钮")
    print("  用于识别'今天已签到'状态, 避免重复点击.")
    ans = input("  今天已经签到过的话输入 y 回车录制; 否则直接回车跳过: ").strip().lower()
    if ans == "y":
        print("  请打开 Trae 账号弹窗, 此时签到按钮应显示为已签到状态.")
        capture("checked_in_button", "录制已签到按钮",
                "请框选弹窗中显示【已签到】的按钮")

    print()
    if ok1 and ok2:
        print("校准完成! 模板已保存到 templates 目录.")
        print("现在可以启动签到机器人了: 双击 run.bat, 或运行 python checkin_bot.py --once 测试.")
    else:
        print("校准未完整完成, 请重新运行本工具, 直到两个必录模板都录制成功.")
    input("\n按回车键退出...")


if __name__ == "__main__":
    main()

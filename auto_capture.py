#!/usr/bin/env python3
"""
Mac 端定时全屏截图工具

直接全屏截图，配合服务器端 slideshow.html 轮播使用。
截图频率高于翻页频率，解码端自动去重。

用法：
    python3 auto_capture.py [截图张数] [间隔秒数] [输出目录]

示例（340 张二维码，服务器每 5 秒翻页）：
    python3 auto_capture.py 1050 2
    python3 auto_capture.py 700 3 ./screenshots/

依赖：无（仅使用 macOS 自带命令）
"""

import os
import sys
import time
import subprocess


def main():
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 1050
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 2
    out_dir = sys.argv[3] if len(sys.argv) > 3 else "./screenshots"

    os.makedirs(out_dir, exist_ok=True)

    total_sec = int(total * interval)
    print("=" * 55)
    print("  QR Code 定时全屏截图工具")
    print("=" * 55)
    print(f"  截图张数:  {total}")
    print(f"  间隔秒数:  {interval} 秒")
    print(f"  输出目录:  {out_dir}/")
    print(f"  预计时间:  约 {total_sec // 60} 分 {total_sec % 60} 秒")
    print("=" * 55)
    print()

    # ---- 防休眠 ----
    caffeine = subprocess.Popen(["caffeinate", "-d", "-i"])
    print("☕ 已启动防休眠 (caffeinate)")
    print()

    # ---- 确认 ----
    print("⚠️  使用前请确保：")
    print("   1. 服务器已关闭屏保: xset s off && xset -dpms")
    print("   2. 服务器浏览器已打开 slideshow.html 并 F11 全屏")
    print("   3. 截图期间不要操作 Mac")
    print()
    input("   准备好后按回车键开始...")

    # ---- 倒计时 ----
    print()
    for i in range(10, 0, -1):
        print(f"   ⏳ {i} 秒后开始截图... （现在点击服务器上的 [开始轮播]）")
        time.sleep(1)

    print()
    print("🚀 开始截图！")
    print()

    # ---- 定时全屏截图 ----
    success = 0
    fail = 0

    for i in range(1, total + 1):
        filename = os.path.join(out_dir, f"shot_{i:04d}.png")

        subprocess.run(["screencapture", "-x", "-C", filename])

        if os.path.exists(filename):
            print(f"  ✅ [{i:>4}/{total}] {filename}")
            success += 1
        else:
            print(f"  ❌ [{i:>4}/{total}] 截图失败")
            fail += 1

        if i < total:
            time.sleep(interval)

    # ---- 完成 ----
    caffeine.terminate()
    print()
    print("☕ 防休眠已释放")
    print()
    print("=" * 55)
    print("  ✨ 截图完成！")
    print("=" * 55)
    print(f"  成功: {success} 张")
    if fail > 0:
        print(f"  失败: {fail} 张")
    print(f"  输出: {out_dir}/")
    print()
    print(f"  下一步: python3 decode_qr.py {out_dir}/ ./restored/")
    print("=" * 55)


if __name__ == "__main__":
    main()

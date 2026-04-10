#!/usr/bin/env python3
"""
SVG 轮播页面生成器（服务器端）

扫描 gen_terminal_qr.py 生成的输出目录，将所有 SVG 内嵌到一个 HTML 页面中，
浏览器打开后全屏自动轮播，配合 Mac 端定时截图使用。

关键修复：强制 SVG 保持 1:1 正方形宽高比，防止浏览器拉伸。

用法:
    python gen_slideshow.py <qr输出目录> [每张显示秒数]

示例:
    python gen_slideshow.py qr_output_my_designs/
    python gen_slideshow.py qr_output_my_designs/ 5
"""

import os
import sys


def collect_svg_files(qr_output_dir):
    """收集输出目录下所有 SVG 文件，递归遍历子目录，按路径排序。"""
    svg_files = []
    for root, dirs, files in os.walk(qr_output_dir):
        dirs.sort()
        for f in sorted(files):
            if f.endswith('.svg'):
                svg_files.append(os.path.join(root, f))
    svg_files.sort()
    return svg_files


def generate_slideshow(qr_output_dir, interval=5):
    """生成轮播 HTML 文件"""

    svg_files = collect_svg_files(qr_output_dir)

    if not svg_files:
        print(f"❌ 在 {qr_output_dir} 下没有找到 SVG 文件")
        sys.exit(1)

    print(f"📂 扫描目录: {qr_output_dir}")
    print(f"🖼️  找到 {len(svg_files)} 个 SVG 文件")
    print(f"⏱️  每张显示: {interval} 秒")
    total_sec = len(svg_files) * interval
    print(f"⏳ 预计总时间: {total_sec // 60} 分 {total_sec % 60} 秒")
    print()

    svg_contents = []
    for fpath in svg_files:
        with open(fpath, 'r', encoding='utf-8') as f:
            svg_contents.append(f.read())
        fname = os.path.basename(os.path.dirname(fpath)) + "/" + os.path.basename(fpath)
        print(f"  ✅ {fname}")

    flash_ms = 500
    display_ms = interval * 1000

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>QR Code Slideshow</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: white;
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    width: 100vw;
    overflow: hidden;
  }}
  #container {{
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    height: 100%;
  }}
  /* 关键修复：强制 SVG 保持正方形 */
  #container svg {{
    /* 取视口宽高中较小的值，确保正方形不超出屏幕 */
    width: min(85vw, 85vh) !important;
    height: min(85vw, 85vh) !important;
    /* 保持宽高比 1:1 */
    aspect-ratio: 1 / 1 !important;
    /* 防止任何拉伸 */
    object-fit: contain;
  }}
  #info {{
    position: fixed;
    top: 10px;
    left: 10px;
    font-family: monospace;
    font-size: 18px;
    color: #333;
    background: rgba(255,255,255,0.9);
    padding: 8px 14px;
    border-radius: 6px;
    z-index: 100;
  }}
  #countdown {{
    position: fixed;
    top: 10px;
    right: 10px;
    font-family: monospace;
    font-size: 16px;
    color: #666;
    background: rgba(255,255,255,0.9);
    padding: 8px 14px;
    border-radius: 6px;
    z-index: 100;
  }}
  #start-overlay {{
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: white;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    z-index: 200;
    font-family: monospace;
  }}
  #start-overlay h1 {{ font-size: 28px; margin-bottom: 20px; }}
  #start-overlay p {{ font-size: 18px; color: #666; margin-bottom: 8px; }}
  #start-btn {{
    margin-top: 30px;
    font-size: 22px;
    padding: 15px 40px;
    cursor: pointer;
    background: #333;
    color: white;
    border: none;
    border-radius: 8px;
  }}
  #start-btn:hover {{ background: #555; }}
  #done {{
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: white;
    align-items: center;
    justify-content: center;
    z-index: 200;
    font-family: monospace;
    font-size: 28px;
  }}
</style>
</head>
<body>

<div id="start-overlay">
  <h1>QR Code Slideshow</h1>
  <p>共 {len(svg_contents)} 张二维码</p>
  <p>每张显示 {interval} 秒</p>
  <p>预计总时间: {total_sec // 60} 分 {total_sec % 60} 秒</p>
  <p style="margin-top:20px; color:#c00;">点击开始后，请立即在 Mac 端启动截图脚本</p>
  <button id="start-btn" onclick="startShow()">▶ 开始轮播</button>
</div>

<div id="info"></div>
<div id="countdown"></div>
<div id="container"></div>
<div id="done">✅ 全部播放完毕！共 {len(svg_contents)} 张</div>

<script>
const svgData = [
"""

    for i, svg in enumerate(svg_contents):
        escaped = svg.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
        html += f"  `{escaped}`"
        if i < len(svg_contents) - 1:
            html += ","
        html += "\n"

    html += f"""];

const DISPLAY_MS = {display_ms};
const FLASH_MS = {flash_ms};
let currentIndex = 0;

function startShow() {{
  document.getElementById('start-overlay').style.display = 'none';
  showNext();
}}

function showNext() {{
  if (currentIndex >= svgData.length) {{
    document.getElementById('container').innerHTML = '';
    document.getElementById('info').style.display = 'none';
    document.getElementById('countdown').style.display = 'none';
    document.getElementById('done').style.display = 'flex';
    return;
  }}

  // 白屏闪烁作为帧分隔
  document.getElementById('container').innerHTML = '';
  document.getElementById('info').textContent = '';

  setTimeout(function() {{
    document.getElementById('container').innerHTML = svgData[currentIndex];

    // 强制修正 SVG 的 viewBox，确保渲染为正方形
    var svgEl = document.querySelector('#container svg');
    if (svgEl) {{
      // 移除可能导致拉伸的 width/height 属性
      svgEl.removeAttribute('width');
      svgEl.removeAttribute('height');
      // 确保 viewBox 存在
      if (!svgEl.getAttribute('viewBox')) {{
        var bbox = svgEl.getBBox();
        svgEl.setAttribute('viewBox', '0 0 ' + bbox.width + ' ' + bbox.height);
      }}
    }}

    document.getElementById('info').textContent =
      '[' + (currentIndex + 1) + '/' + svgData.length + ']';

    var remaining = DISPLAY_MS / 1000;
    document.getElementById('countdown').textContent = remaining + 's';
    var timer = setInterval(function() {{
      remaining--;
      if (remaining > 0) {{
        document.getElementById('countdown').textContent = remaining + 's';
      }} else {{
        clearInterval(timer);
      }}
    }}, 1000);

    currentIndex++;
    setTimeout(showNext, DISPLAY_MS);
  }}, FLASH_MS);
}}
</script>
</body>
</html>
"""

    out_path = os.path.join(qr_output_dir, "slideshow.html")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print()
    print("=" * 55)
    print(f"✨ 生成成功！")
    print(f"📄 轮播文件: {out_path}")
    print(f"🖼️  共 {len(svg_contents)} 张二维码")
    print()
    print("使用步骤:")
    print(f"  1. 服务器关闭屏保: xset s off && xset -dpms")
    print(f"  2. 打开轮播: firefox {out_path}")
    print(f"  3. 按 F11 进入全屏")
    print(f"  4. 在 Mac 端启动截图脚本")
    print(f"  5. 点击页面上的 [开始轮播]")
    print("=" * 55)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python gen_slideshow.py <qr输出目录> [每张显示秒数]")
        print()
        print("示例:")
        print("  python gen_slideshow.py qr_output_my_designs/")
        print("  python gen_slideshow.py qr_output_my_designs/ 5")
        sys.exit(1)

    qr_dir = sys.argv[1]
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    if not os.path.isdir(qr_dir):
        print(f"❌ 目录不存在: {qr_dir}")
        sys.exit(1)

    generate_slideshow(qr_dir, interval=interval)

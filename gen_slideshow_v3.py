#!/usr/bin/env python3
"""
SVG 矩阵轮播页面生成器（服务器端）- 宽屏全量利用版

将扫描到的 SVG 按照指定的行列数以 CSS Grid 矩阵形式内嵌到一个 HTML 中。
已破除正方形容器限制，完美适配 16:9 等宽屏显示器。
"""

import os
import sys
import math

def collect_svg_files(qr_output_dir):
    svg_files = []
    for root, dirs, files in os.walk(qr_output_dir):
        dirs.sort()
        for f in sorted(files):
            if f.endswith('.svg'):
                svg_files.append(os.path.join(root, f))
    svg_files.sort()
    return svg_files

def generate_slideshow(qr_output_dir, interval=5, cols=5, rows=3):
    svg_files = collect_svg_files(qr_output_dir)

    if not svg_files:
        print(f"❌ 在 {qr_output_dir} 下没有找到 SVG 文件")
        sys.exit(1)

    batch_size = cols * rows
    total_frames = math.ceil(len(svg_files) / batch_size)
    total_sec = total_frames * interval

    print(f"📂 扫描目录: {qr_output_dir}")
    print(f"🖼️  找到 {len(svg_files)} 个 SVG 文件")
    print(f"🔲 矩阵布局: {cols} 列 x {rows} 行 (每屏 {batch_size} 张)")
    print(f"⏱️  每屏显示: {interval} 秒")
    print(f"⏳ 预计总时间: {total_sec // 60} 分 {total_sec % 60} 秒 ({total_frames} 帧)")
    print()

    svg_contents = []
    for fpath in svg_files:
        with open(fpath, 'r', encoding='utf-8') as f:
            svg_contents.append(f.read())

    flash_ms = 500
    display_ms = interval * 1000

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>QR Code Matrix Slideshow</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: white;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100vh;
    width: 100vw;
    overflow: hidden;
  }}
  #container {{
    display: grid;
    /* 使用 minmax(0, 1fr) 防止内置宽高的 SVG 撑爆网格 */
    grid-template-columns: repeat({cols}, minmax(0, 1fr));
    grid-template-rows: repeat({rows}, minmax(0, 1fr));
    gap: 1.5vw; 
    width: 96vw;  /* 横向充分吃满屏幕 */
    height: 85vh; /* 纵向留出顶部文字的空间 */
    margin-top: 2vh;
  }}
  #container > div {{
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }}
  /* 强制 SVG 作为响应式内容 */
  #container svg {{
    max-width: 100%;
    max-height: 100%;
    width: auto;
    height: auto;
    object-fit: contain;
  }}
  #info {{
    position: fixed;
    top: 10px;
    left: 15px;
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
    right: 15px;
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
  <h1>QR Code Matrix Slideshow</h1>
  <p>共 {len(svg_contents)} 张二维码 | 布局: {cols}x{rows}</p>
  <button id="start-btn" onclick="startShow()">▶ 开始轮播</button>
</div>

<div id="info"></div>
<div id="countdown"></div>
<div id="container"></div>
<div id="done">✅ 全部播放完毕！</div>

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
const BATCH_SIZE = {batch_size};
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

  document.getElementById('container').innerHTML = '';
  document.getElementById('info').textContent = '';

  setTimeout(function() {{
    let htmlChunk = '';
    let currentBatch = 0;
    for(let i = 0; i < BATCH_SIZE; i++) {{
        if(currentIndex + i < svgData.length) {{
            htmlChunk += '<div>' + svgData[currentIndex + i] + '</div>';
            currentBatch++;
        }}
    }}
    document.getElementById('container').innerHTML = htmlChunk;

    // 完美重塑 SVG 比例：提取自带宽高，转换为 viewBox，让 CSS 完全接管尺寸
    var svgs = document.querySelectorAll('#container svg');
    svgs.forEach(function(svgEl) {{
      if (!svgEl.getAttribute('viewBox')) {{
        var w = svgEl.getAttribute('width');
        var h = svgEl.getAttribute('height');
        if (w && h) {{
            svgEl.setAttribute('viewBox', '0 0 ' + parseInt(w) + ' ' + parseInt(h));
        }} else {{
            var bbox = svgEl.getBBox();
            svgEl.setAttribute('viewBox', '0 0 ' + bbox.width + ' ' + bbox.height);
        }}
      }}
      svgEl.removeAttribute('width');
      svgEl.removeAttribute('height');
      svgEl.style.width = '100%';
      svgEl.style.height = '100%';
    }});

    let startIdx = currentIndex + 1;
    let endIdx = currentIndex + currentBatch;
    document.getElementById('info').textContent =
      '[' + startIdx + '-' + endIdx + ' / ' + svgData.length + ']';

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

    currentIndex += BATCH_SIZE;
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
    print(f"📄 轮播文件已生成: {out_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python gen_slideshow_matrix.py <qr输出目录> [显示秒数] [列数] [行数]")
        print("示例:")
        print("  默认 (5秒 5x3矩阵): python gen_slideshow_matrix.py qr_output/")
        print("  自定义 (3秒 4x3矩阵): python gen_slideshow_matrix.py qr_output/ 3 4 3")
        sys.exit(1)

    qr_dir = sys.argv[1]
    
    # 按照 顺序读取，若缺省则使用默认值
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    cols = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    rows = int(sys.argv[4]) if len(sys.argv) > 4 else 3

    if not os.path.isdir(qr_dir):
        print(f"❌ 目录不存在: {qr_dir}")
        sys.exit(1)

    generate_slideshow(qr_dir, interval=interval, cols=cols, rows=rows)
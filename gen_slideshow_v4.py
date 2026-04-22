#!/usr/bin/env python3
"""
SVG/PNG 矩阵轮播页面生成器 (外部引用版)

改动：
1. 不再读取文件内容，仅记录相对路径。
2. HTML 内部改用 <img> 标签引用外部文件，解决大文件导致浏览器崩溃的问题。
"""

import os
import sys
import math
import json

def collect_image_files(qr_output_dir):
    """收集目录下所有的图片文件路径 (相对于 qr_output_dir)

    清单和哨兵 QR 码会被提到最前面，并在末尾重复一次，
    以最大限度保证即使截图中断也能捕获到清单信息。
    """
    img_files = []
    # 扫描 svg 和 png
    for root, dirs, files in os.walk(qr_output_dir):
        dirs.sort()
        for f in sorted(files):
            if f.lower().endswith(('.svg', '.png')):
                # 计算相对于输出目录的路径，确保 HTML 内部引用正确
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, qr_output_dir)
                img_files.append(rel_path)

    # 将清单(_manifest.txt)和哨兵(_manifest_count.txt)提到最前面，
    # 并在末尾重复一次，确保截图中断时仍能捕获清单信息
    meta_prefixes = ('_manifest.txt', '_manifest_count.txt')
    meta_imgs = [p for p in img_files if any(p.startswith(pfx) for pfx in meta_prefixes)]
    data_imgs = [p for p in img_files if not any(p.startswith(pfx) for pfx in meta_prefixes)]

    if meta_imgs:
        return meta_imgs + data_imgs + meta_imgs
    return data_imgs

def generate_slideshow(qr_output_dir, interval=5, cols=5, rows=3):
    img_relative_paths = collect_image_files(qr_output_dir)

    if not img_relative_paths:
        print(f"❌ 在 {qr_output_dir} 下没有找到图片文件")
        sys.exit(1)

    batch_size = cols * rows
    total_frames = math.ceil(len(img_relative_paths) / batch_size)
    total_sec = total_frames * interval

    # 统计清单/哨兵重复的数量
    meta_prefixes = ('_manifest.txt', '_manifest_count.txt')
    meta_count = sum(1 for p in img_relative_paths if any(p.startswith(pfx) for pfx in meta_prefixes))
    # 重复部分占一半（前置+尾部各一份）
    meta_unique = meta_count // 2 if meta_count > 0 else 0
    data_count = len(img_relative_paths) - meta_count

    print(f"📂 扫描目录: {qr_output_dir}")
    print(f"🖼️  找到 {data_count + meta_unique} 个图片文件")
    if meta_unique > 0:
        print(f"📋 清单二维码首尾重复播放: {meta_unique} 张 × 2 = {meta_count} 张")
    print(f"🔲 矩阵布局: {cols} 列 x {rows} 行 (每屏 {batch_size} 张)")
    print(f"⏱️  每屏显示: {interval} 秒")
    print(f"⏳ 预计总时间: {total_sec // 60} 分 {total_sec % 60} 秒 ({total_frames} 帧)")
    print()

    # 将路径列表转换为 JS 数组字符串
    # 使用 json.dumps 确保路径中的特殊字符(如反斜杠)被正确转义
    js_paths_array = json.dumps(img_relative_paths, indent=2)

    display_ms = interval * 1000
    flash_ms = 500

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>QR Code Matrix Slideshow (External)</title>
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
    grid-template-columns: repeat({cols}, minmax(0, 1fr));
    grid-template-rows: repeat({rows}, minmax(0, 1fr));
    gap: 1.5vw; 
    width: 96vw;
    height: 85vh;
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
  /* 使用 img 标签并开启优化渲染 */
  #container img {{
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
    image-rendering: pixelated; /* 保持二维码锐利 */
  }}
  #info, #countdown {{
    position: fixed;
    top: 10px;
    font-family: monospace;
    font-size: 18px;
    color: #333;
    background: rgba(255,255,255,0.9);
    padding: 8px 14px;
    border-radius: 6px;
    z-index: 100;
  }}
  #info {{ left: 15px; }}
  #countdown {{ right: 15px; color: #666; font-size: 16px; }}
  
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
  <p>共 {len(img_relative_paths)} 张二维码 | 布局: {cols}x{rows} | 模式: 外部加载</p>
  <button id="start-btn" onclick="startShow()">▶ 开始轮播</button>
</div>

<div id="info"></div>
<div id="countdown"></div>
<div id="container"></div>
<div id="done">✅ 全部播放完毕！</div>

<script>
// 这里存储的是相对路径，HTML 体积非常小
const imgPaths = {js_paths_array};

const DISPLAY_MS = {display_ms};
const FLASH_MS = {flash_ms};
const BATCH_SIZE = {batch_size};
let currentIndex = 0;

function startShow() {{
  document.getElementById('start-overlay').style.display = 'none';
  showNext();
}}

function showNext() {{
  if (currentIndex >= imgPaths.length) {{
    document.getElementById('container').innerHTML = '';
    document.getElementById('info').style.display = 'none';
    document.getElementById('countdown').style.display = 'none';
    document.getElementById('done').style.display = 'flex';
    return;
  }}

  // 闪烁间隔，清空容器
  document.getElementById('container').innerHTML = '';
  document.getElementById('info').textContent = '';

  setTimeout(function() {{
    let currentBatchCount = 0;
    
    for(let i = 0; i < BATCH_SIZE; i++) {{
        let idx = currentIndex + i;
        if(idx < imgPaths.length) {{
            let div = document.createElement('div');
            let img = document.createElement('img');
            // 核心：通过 src 引用外部文件，浏览器会高效处理内存
            img.src = imgPaths[idx];
            div.appendChild(img);
            document.getElementById('container').appendChild(div);
            currentBatchCount++;
        }}
    }}

    let startIdx = currentIndex + 1;
    let endIdx = currentIndex + currentBatchCount;
    document.getElementById('info').textContent =
      '[' + startIdx + '-' + endIdx + ' / ' + imgPaths.length + ']';

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
    print(f"💡 提示：请确保 HTML 文件与图片目录保持相对位置不变。")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python gen_slideshow_v4.py <qr输出目录> [显示秒数] [列数] [行数]")
        sys.exit(1)

    qr_dir = sys.argv[1]
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    cols = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    rows = int(sys.argv[4]) if len(sys.argv) > 4 else 3

    if not os.path.isdir(qr_dir):
        print(f"❌ 目录不存在: {qr_dir}")
        sys.exit(1)

    generate_slideshow(qr_dir, interval=interval, cols=cols, rows=rows)
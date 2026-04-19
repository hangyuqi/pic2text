#!/usr/bin/env python3
"""
补丁轮播生成器（服务器端）- 支持参数配置

用于在发现丢帧后，仅将缺失的二维码片段重新打包为 HTML 轮播页面。

用法: 
    python gen_patch_slideshow.py <qr输出目录> [选项] <缺失序号1> <缺失序号2> ...

选项:
    -i, --interval  每屏显示秒数 (默认: 5)
    -c, --cols      矩阵列数 (默认: 3)
    -r, --rows      矩阵行数 (默认: 3)

示例: 
    # 默认 3x3 矩阵，5秒间隔
    python gen_patch_slideshow.py qr_output_pdf/ 2549 2585 3481
    
    # 自定义 2x2 矩阵，每屏 3 秒
    python gen_patch_slideshow.py qr_output_pdf/ -c 2 -r 2 -i 3 2549 2585 3481
"""

import os
import sys
import argparse

def generate_patch_slideshow(qr_output_dir, missing_indices, cols=3, rows=3, interval=5):
    svg_contents = []
    
    print(f"📂 扫描目录: {qr_output_dir}")
    print(f"🔲 矩阵配置: {cols} 列 x {rows} 行 (间隔: {interval}秒)")
    print(f"🔍 正在提取 {len(missing_indices)} 个缺失片段...")
    
    for idx in missing_indices:
        # 兼容原脚本的 part_XXX.svg 命名规则
        try:
            fname = f"part_{int(idx):03d}.svg" 
        except ValueError:
            print(f"  ⚠️ 无效的序号格式，已跳过: {idx}")
            continue
            
        # 遍历子目录寻找该文件
        found = False
        for root, _, files in os.walk(qr_output_dir):
            if fname in files:
                fpath = os.path.join(root, fname)
                with open(fpath, 'r', encoding='utf-8') as f:
                    svg_contents.append(f.read())
                print(f"  ✅ 找到: {os.path.relpath(fpath, qr_output_dir)}")
                found = True
                break
        
        if not found:
            print(f"  ⚠️ 未找到片段: {fname}")

    if not svg_contents:
        print("❌ 没有找到任何指定的 SVG 文件，请检查目录和序号。")
        sys.exit(1)

    # ---------------------------------------------------------
    # HTML 生成逻辑
    # ---------------------------------------------------------
    batch_size = cols * rows
    flash_ms = 500
    display_ms = interval * 1000

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>QR Code Patch Slideshow</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: white; display: flex; align-items: center; justify-content: center; height: 100vh; width: 100vw; overflow: hidden; }}
  #container {{ display: grid; grid-template-columns: repeat({cols}, 1fr); grid-template-rows: repeat({rows}, 1fr); gap: 1.5vmin; width: 95vmin; height: 95vmin; aspect-ratio: {cols} / {rows}; }}
  #container > div {{ display: flex; align-items: center; justify-content: center; background: white; }}
  #container svg {{ width: 100% !important; height: 100% !important; object-fit: contain; }}
  #info, #countdown {{ position: fixed; top: 10px; font-family: monospace; font-size: 16px; color: #333; background: rgba(255,255,255,0.9); padding: 8px 14px; border-radius: 6px; z-index: 100; }}
  #info {{ left: 10px; }} #countdown {{ right: 10px; }}
  #start-overlay {{ position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: white; display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 200; font-family: monospace; }}
  #start-btn {{ margin-top: 30px; font-size: 22px; padding: 15px 40px; cursor: pointer; background: #333; color: white; border: none; border-radius: 8px; }}
  #done {{ display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: white; align-items: center; justify-content: center; z-index: 200; font-family: monospace; font-size: 28px; }}
</style>
</head>
<body>
<div id="start-overlay">
  <h1>Patch Slideshow</h1>
  <p>共 {len(svg_contents)} 张补丁二维码</p>
  <button id="start-btn" onclick="startShow()">▶ 开始轮播</button>
</div>
<div id="info"></div><div id="countdown"></div><div id="container"></div>
<div id="done">✅ 补丁播放完毕！</div>
<script>
const svgData = [
"""
    for i, svg in enumerate(svg_contents):
        escaped = svg.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
        html += f"  `{escaped}`" + ("," if i < len(svg_contents) - 1 else "") + "\n"

    html += f"""];
const DISPLAY_MS = {display_ms}; const FLASH_MS = {flash_ms}; const BATCH_SIZE = {batch_size};
let currentIndex = 0;
function startShow() {{ document.getElementById('start-overlay').style.display = 'none'; showNext(); }}
function showNext() {{
  if (currentIndex >= svgData.length) {{
    document.getElementById('container').innerHTML = ''; document.getElementById('info').style.display = 'none'; document.getElementById('countdown').style.display = 'none'; document.getElementById('done').style.display = 'flex'; return;
  }}
  document.getElementById('container').innerHTML = ''; document.getElementById('info').textContent = '';
  setTimeout(function() {{
    let htmlChunk = ''; let currentBatch = 0;
    for(let i = 0; i < BATCH_SIZE; i++) {{
        if(currentIndex + i < svgData.length) {{ htmlChunk += '<div>' + svgData[currentIndex + i] + '</div>'; currentBatch++; }}
    }}
    document.getElementById('container').innerHTML = htmlChunk;
    var svgs = document.querySelectorAll('#container svg');
    svgs.forEach(function(svgEl) {{
      svgEl.removeAttribute('width'); svgEl.removeAttribute('height');
      if (!svgEl.getAttribute('viewBox')) {{ var bbox = svgEl.getBBox(); svgEl.setAttribute('viewBox', '0 0 ' + bbox.width + ' ' + bbox.height); }}
    }});
    
    let startIdx = currentIndex + 1;
    let endIdx = currentIndex + currentBatch;
    document.getElementById('info').textContent = '补丁: [' + startIdx + '-' + endIdx + ' / ' + svgData.length + ']';
    
    var remaining = DISPLAY_MS / 1000; document.getElementById('countdown').textContent = remaining + 's';
    var timer = setInterval(function() {{ remaining--; if(remaining > 0) document.getElementById('countdown').textContent = remaining + 's'; else clearInterval(timer); }}, 1000);
    currentIndex += BATCH_SIZE; setTimeout(showNext, DISPLAY_MS);
  }}, FLASH_MS);
}}
</script>
</body>
</html>
"""

    out_path = os.path.join(qr_output_dir, "patch_slideshow.html")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n✨ 补丁轮播文件已生成: {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成针对缺失二维码片段的补丁轮播页面。")
    parser.add_argument("qr_dir", help="包含生成的SVG二维码的根目录")
    parser.add_argument("-i", "--interval", type=int, default=5, help="每屏显示秒数 (默认: 5)")
    parser.add_argument("-c", "--cols", type=int, default=3, help="矩阵列数 (默认: 3)")
    parser.add_argument("-r", "--rows", type=int, default=3, help="矩阵行数 (默认: 3)")
    parser.add_argument("indices", nargs="+", help="缺失的片段序号列表 (如: 12 45 67)")

    args = parser.parse_args()

    if not os.path.isdir(args.qr_dir):
        print(f"❌ 目录不存在: {args.qr_dir}")
        sys.exit(1)

    generate_patch_slideshow(args.qr_dir, args.indices, cols=args.cols, rows=args.rows, interval=args.interval)
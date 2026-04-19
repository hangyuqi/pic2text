#!/usr/bin/env python3
import os
import sys
import urllib.parse
import math

def collect_svg_files(qr_output_dir):
    """收集输出目录下所有 SVG 文件"""
    svg_files = []
    for root, dirs, files in os.walk(qr_output_dir):
        dirs.sort()
        for f in sorted(files):
            if f.endswith('.svg'):
                svg_files.append(os.path.join(root, f))
    svg_files.sort()
    return svg_files

def generate_slideshow(qr_output_dir, fps=15):
    """生成基于图片预加载和 V-Sync 同步的视频流 HTML"""

    svg_files = collect_svg_files(qr_output_dir)

    if not svg_files:
        print(f"❌ 在 {qr_output_dir} 下没有找到 SVG 文件")
        sys.exit(1)

    print(f"📂 扫描目录: {qr_output_dir}")
    print(f"🖼️  找到 {len(svg_files)} 个 SVG 文件")
    print(f"⚡ 目标帧率: {fps} FPS")
    
    total_sec = len(svg_files) / fps
    mins = int(total_sec // 60)
    secs = int(total_sec % 60)
    print(f"⏳ 预计总时间: {mins} 分 {secs} 秒")
    print()

    # 关键优化：在 Python 端直接把 SVG 转成 Data URI，避免浏览器用 CPU 高频解析 SVG DOM
    data_uris = []
    print("📦 正在预处理 SVG 加速渲染流...")
    for i, fpath in enumerate(svg_files):
        with open(fpath, 'r', encoding='utf-8') as f:
            svg_content = f.read()
            # 转义为浏览器 <img src="..."> 可直接读取的格式
            encoded = urllib.parse.quote(svg_content)
            data_uris.append(f"data:image/svg+xml;charset=utf-8,{encoded}")
            
        if (i + 1) % 100 == 0:
            print(f"  已处理 {i + 1}/{len(svg_files)} 张...")

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>QR Code Video Stream</title>
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
  #qr-img {{
    width: min(85vw, 85vh);
    height: min(85vw, 85vh);
    object-fit: contain;
    display: none; /* 初始隐藏 */
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
    display: none;
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
    display: none;
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
  <h1>QR Code Video Stream</h1>
  <p>共 {len(svg_files)} 张二维码</p>
  <p>目标帧率: <strong style="color:red;">{fps} FPS</strong></p>
  <p>预计总时长: {mins} 分 {secs} 秒</p>
  <p style="margin-top:20px; color:#c00;">准备好 Mac 端录屏软件后，点击开始</p>
  <button id="start-btn" onclick="startShow()">▶ 开始播放</button>
</div>

<div id="info"></div>
<div id="countdown"></div>
<img id="qr-img" src="" alt="QR Stream" />
<div id="done">✅ 全部播放完毕！共 {len(svg_files)} 张</div>

<script>
// 高效的 Base64 预加载数组，避开了 innerHTML 的重绘地狱
const svgData = [
"""

    for i, uri in enumerate(data_uris):
        html += f"  \"{uri}\""
        if i < len(data_uris) - 1:
            html += ","
        html += "\n"

    html += f"""];

const TARGET_FPS = {fps};
const FRAME_INTERVAL = 1000 / TARGET_FPS;
let currentIndex = 0;
let lastTime = 0;
let requestID;

function startShow() {{
  document.getElementById('start-overlay').style.display = 'none';
  document.getElementById('qr-img').style.display = 'block';
  document.getElementById('info').style.display = 'block';
  document.getElementById('countdown').style.display = 'block';
  
  // 启动高频渲染循环
  requestID = requestAnimationFrame(renderLoop);
}}

function renderLoop(timestamp) {{
  if (currentIndex >= svgData.length) {{
    cancelAnimationFrame(requestID);
    document.getElementById('qr-img').style.display = 'none';
    document.getElementById('info').style.display = 'none';
    document.getElementById('countdown').style.display = 'none';
    document.getElementById('done').style.display = 'flex';
    return;
  }}

  if (!lastTime) lastTime = timestamp;
  const elapsed = timestamp - lastTime;

  if (elapsed >= FRAME_INTERVAL) {{
    // 仅仅是替换 src 属性，浏览器硬件解码，速度极快
    document.getElementById('qr-img').src = svgData[currentIndex];

    // 更新界面信息
    document.getElementById('info').textContent = '[' + (currentIndex + 1) + '/' + svgData.length + ']';
    
    let remainingSec = Math.ceil(((svgData.length - currentIndex) * FRAME_INTERVAL) / 1000);
    document.getElementById('countdown').textContent = remainingSec + 's';

    currentIndex++;
    
    // 扣除掉间隔时间，保留剩余的误差，防止整体时间漂移
    lastTime = timestamp - (elapsed % FRAME_INTERVAL);
  }}

  requestID = requestAnimationFrame(renderLoop);
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
    print(f"✨ 高帧率视频流生成成功！")
    print(f"📄 轮播文件: {out_path}")
    print(f"⚡ 渲染速度: {fps} 帧/秒")
    print("=" * 55)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python gen_slideshow_v3.py <qr输出目录> [FPS帧率]")
        print("示例: python gen_slideshow_v3.py qr_output_my_designs/ 15")
        sys.exit(1)

    qr_dir = sys.argv[1]
    # 默认 15 FPS
    fps = int(sys.argv[2]) if len(sys.argv) > 2 else 15

    if not os.path.isdir(qr_dir):
        print(f"❌ 目录不存在: {qr_dir}")
        sys.exit(1)

    generate_slideshow(qr_dir, fps=fps)
#!/usr/bin/env python3
"""
补丁轮播生成器（服务器端）- 基于文件夹精准匹配 (支持 |ALL 整文件读取)

用于在发现丢帧后，读取包含文件名的报告文件，
先定位目标文件专属的二维码文件夹，再提取缺失片段生成 HTML 轮播。

用法: 
    python gen_patch_slideshow.py <qr输出目录> -f <丢失报告文件> [选项]

选项:
    -i, --interval  每屏显示秒数 (默认: 5)
    -c, --cols      矩阵列数 (默认: 3)
    -r, --rows      矩阵行数 (默认: 3)
    -f, --file      读取 decode_qr 导出的 missing_patches.txt 文件 (格式: fname|1,2,3 或 fname|ALL)
"""

import os
import sys
import argparse

def generate_patch_slideshow(qr_output_dir, missing_tasks, cols=3, rows=3, interval=5):
    """
    missing_tasks: list of tuples -> [(fname, idx), (fname, 'ALL'), ...]
    """
    svg_contents = []
    
    print(f"📂 扫描目录: {qr_output_dir}")
    print(f"🔲 矩阵配置: {cols} 列 x {rows} 行 (间隔: {interval}秒)")
    print(f"🔍 正在执行精准提取策略...")

    # 1. 将任务按文件名分组，避免重复查找文件夹
    # 结构: { 'docker_image.tar': [12, 45, 67], 'script.sh': ['ALL'], ... }
    tasks_by_file = {}
    for fname, idx in missing_tasks:
        if fname not in tasks_by_file:
            tasks_by_file[fname] = []
        tasks_by_file[fname].append(idx)

    # 2. 预扫描子目录，建立所有目录路径的列表 (放弃有缺陷的字典覆写机制)
    all_dirs = []
    for root, dirs, _ in os.walk(qr_output_dir):
        all_dirs.append(root)

    # 3. 按文件粒度进行精准提取
    for fname, indices in tasks_by_file.items():
        pure_fname = os.path.basename(fname) 
        fname_base = os.path.splitext(pure_fname)[0]
        
        target_dir = None
        
        # 优先级1：尝试直接拼接完整的相对路径 (如果二维码输出保留了原始目录树)
        exact_path = os.path.normpath(os.path.join(qr_output_dir, fname))
        if os.path.isdir(exact_path):
            target_dir = exact_path
        else:
            # 优先级2：在所有目录中搜寻，处理可能被压平(Flatten)的目录或同名冲突
            candidates = []
            for dpath in all_dirs:
                dname = os.path.basename(dpath)
                # 严格限制：文件夹名必须完全一致，防止短名匹配到长名
                if dname == pure_fname or dname == fname_base:
                    candidates.append(dpath)
            
            if candidates:
                # 解决同名文件冲突：利用缺失报告中的父目录特征(如 ncv 或 vcs)进行二次筛选
                target_dir = candidates[0] # 默认取第一个
                norm_fname = os.path.normpath(fname)
                for cand in candidates:
                    if norm_fname in os.path.normpath(cand):
                        target_dir = cand
                        break
        
        if not target_dir:
            print(f"  ⚠️ 未找到 [{fname}] 对应的存放目录，已跳过")
            continue
        
        # 4. 确定要提取的最终序号集合
        target_indices = set()
        if 'ALL' in indices:
            # 扫描目录下所有的 part_xxx.svg
            for filename in os.listdir(target_dir):
                if filename.startswith("part_") and filename.endswith(".svg"):
                    try:
                        # 从 "part_001.svg" 中提取数字 1
                        idx = int(filename[5:-4])
                        target_indices.add(idx)
                    except ValueError:
                        pass
            print(f"    🔄 识别到 ALL 标记，自动读取该目录下所有片段 (共 {len(target_indices)} 个)")
        else:
            target_indices = set(indices)

        # 5. 在确定且唯一的目录下直接读取特定序号
        for idx in sorted(target_indices):
            target_part = f"part_{int(idx):03d}.svg"
            fpath = os.path.join(target_dir, target_part)
            
            if os.path.exists(fpath):
                with open(fpath, 'r', encoding='utf-8') as f:
                    svg_contents.append(f.read())
                print(f"    ✅ 提取成功: [{idx}] -> {target_part}")
            else:
                print(f"    ⚠️ 片段丢失: [{idx}] -> {target_part} (该文件在目录中不存在)")

    if not svg_contents:
        print("\n❌ 没有找到任何指定的 SVG 文件，请检查目录和报告内容。")
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
    parser.add_argument("-f", "--file", required=True, help="必需: 读取 decode_qr 导出的 missing_patches.txt 文件")

    args = parser.parse_args()

    if not os.path.isdir(args.qr_dir):
        print(f"❌ 目录不存在: {args.qr_dir}")
        sys.exit(1)
        
    missing_tasks = []
    
    if os.path.exists(args.file):
        with open(args.file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or '|' not in line: 
                    continue
                parts = line.split('|', 1)
                fname = parts[0].strip()
                indices_str = parts[1].strip().upper()
                
                # 新增：处理 ALL 标记
                if indices_str == 'ALL':
                    missing_tasks.append((fname, 'ALL'))
                else:
                    for idx_str in indices_str.split(','):
                        idx_str = idx_str.strip()
                        if idx_str.isdigit():
                            missing_tasks.append((fname, int(idx_str)))
    else:
        print(f"❌ 报告文件不存在: {args.file}")
        sys.exit(1)

    if not missing_tasks:
        print("❌ 错误：报告文件为空或格式不正确。")
        sys.exit(1)
        
    generate_patch_slideshow(args.qr_dir, missing_tasks, cols=args.cols, rows=args.rows, interval=args.interval)
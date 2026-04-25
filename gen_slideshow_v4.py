#!/usr/bin/env python3
"""
SVG/PNG 矩阵轮播页面生成器 (外部引用版)

改动：
1. 不再读取文件内容，仅记录相对路径。
2. HTML 内部改用 <img> 标签引用外部文件，解决大文件导致浏览器崩溃的问题。
3. 新增补丁模式 (-f)：根据 missing_patches.txt 仅输出缺失片段，
   复用相同的外部引用 + 自适应矩阵排版逻辑。
"""

import os
import sys
import math
import json
import argparse

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


def collect_patch_image_files(qr_output_dir, missing_tasks):
    """根据 missing_tasks 在 qr_output_dir 中精准定位缺失片段，
    返回相对于 qr_output_dir 的图片路径列表 (优先 svg，其次 png)。

    missing_tasks: list of tuples -> [(fname, idx), (fname, 'ALL'), ...]
    """
    rel_paths = []

    # 1. 按文件名分组
    tasks_by_file = {}
    for fname, idx in missing_tasks:
        tasks_by_file.setdefault(fname, []).append(idx)

    # 2. 预扫描所有目录路径
    all_dirs = []
    for root, _dirs, _files in os.walk(qr_output_dir):
        all_dirs.append(root)

    # 3. 按文件粒度精准提取
    for fname, indices in tasks_by_file.items():
        pure_fname = os.path.basename(fname)
        fname_base = os.path.splitext(pure_fname)[0]

        target_dir = None

        # 优先级1：直接拼接完整相对路径
        exact_path = os.path.normpath(os.path.join(qr_output_dir, fname))
        if os.path.isdir(exact_path):
            target_dir = exact_path
        else:
            # 优先级2：在所有目录中严格匹配文件夹名
            candidates = []
            for dpath in all_dirs:
                dname = os.path.basename(dpath)
                if dname == pure_fname or dname == fname_base:
                    candidates.append(dpath)

            if candidates:
                target_dir = candidates[0]
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
            for filename in os.listdir(target_dir):
                low = filename.lower()
                if filename.startswith("part_") and (low.endswith(".svg") or low.endswith(".png")):
                    try:
                        # 去掉 "part_" 前缀和扩展名后转 int
                        stem = os.path.splitext(filename)[0]
                        idx = int(stem[5:])
                        target_indices.add(idx)
                    except ValueError:
                        pass
            print(f"    🔄 识别到 ALL 标记，自动读取该目录下所有片段 (共 {len(target_indices)} 个)")
        else:
            target_indices = set(i for i in indices if isinstance(i, int))

        # 5. 在确定的目录下读取特定序号 (优先 svg，其次 png)
        for idx in sorted(target_indices):
            found = None
            for ext in ('.svg', '.png'):
                target_part = f"part_{int(idx):03d}{ext}"
                fpath = os.path.join(target_dir, target_part)
                if os.path.exists(fpath):
                    found = fpath
                    break

            if found:
                rel_path = os.path.relpath(found, qr_output_dir)
                rel_paths.append(rel_path)
                print(f"    ✅ 提取成功: [{idx}] -> {os.path.basename(found)}")
            else:
                print(f"    ⚠️ 片段丢失: [{idx}] -> part_{int(idx):03d}.* (该文件在目录中不存在)")

    return rel_paths


def parse_missing_file(file_path):
    """解析 missing_patches.txt，返回 [(fname, idx_or_'ALL'), ...]"""
    missing_tasks = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or '|' not in line:
                continue
            parts = line.split('|', 1)
            fname = parts[0].strip()
            indices_str = parts[1].strip().upper()

            if indices_str == 'ALL':
                missing_tasks.append((fname, 'ALL'))
            else:
                for idx_str in indices_str.split(','):
                    idx_str = idx_str.strip()
                    if idx_str.isdigit():
                        missing_tasks.append((fname, int(idx_str)))
    return missing_tasks


def generate_slideshow(qr_output_dir, interval=5, cols=5, rows=3,
                      missing_file=None, output_name=None):
    is_patch_mode = missing_file is not None

    if is_patch_mode:
        missing_tasks = parse_missing_file(missing_file)
        if not missing_tasks:
            print("❌ 错误：报告文件为空或格式不正确。")
            sys.exit(1)
        print(f"📂 扫描目录: {qr_output_dir}")
        print(f"🔍 正在执行精准提取策略 (补丁模式)...")
        img_relative_paths = collect_patch_image_files(qr_output_dir, missing_tasks)
        title = "QR Code Patch Slideshow (External)"
        header = "Patch Slideshow"
        info_prefix = "补丁: "
        done_text = "✅ 补丁播放完毕！"
        default_out = "patch_slideshow.html"
    else:
        img_relative_paths = collect_image_files(qr_output_dir)
        title = "QR Code Matrix Slideshow (External)"
        header = "QR Code Matrix Slideshow"
        info_prefix = ""
        done_text = "✅ 全部播放完毕！"
        default_out = "slideshow.html"

    if not img_relative_paths:
        print(f"❌ 在 {qr_output_dir} 下没有找到{'缺失' if is_patch_mode else ''}图片文件")
        sys.exit(1)

    batch_size = cols * rows
    total_frames = math.ceil(len(img_relative_paths) / batch_size)
    total_sec = total_frames * interval

    if not is_patch_mode:
        # 统计清单/哨兵重复的数量
        meta_prefixes = ('_manifest.txt', '_manifest_count.txt')
        meta_count = sum(1 for p in img_relative_paths if any(p.startswith(pfx) for pfx in meta_prefixes))
        meta_unique = meta_count // 2 if meta_count > 0 else 0
        data_count = len(img_relative_paths) - meta_count
        print(f"📂 扫描目录: {qr_output_dir}")
        print(f"🖼️  找到 {data_count + meta_unique} 个图片文件")
        if meta_unique > 0:
            print(f"📋 清单二维码首尾重复播放: {meta_unique} 张 × 2 = {meta_count} 张")
    else:
        print(f"🖼️  共提取 {len(img_relative_paths)} 张补丁二维码")

    print(f"🔲 矩阵布局: {cols} 列 x {rows} 行 (每屏 {batch_size} 张)")
    print(f"⏱️  每屏显示: {interval} 秒")
    print(f"⏳ 预计总时间: {total_sec // 60} 分 {total_sec % 60} 秒 ({total_frames} 帧)")
    print()

    # 将路径列表转换为 JS 数组字符串
    js_paths_array = json.dumps(img_relative_paths, indent=2)

    display_ms = interval * 1000
    flash_ms = 500

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
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
  <h1>{header}</h1>
  <p>共 {len(img_relative_paths)} 张二维码 | 布局: {cols}x{rows} | 模式: 外部加载</p>
  <button id="start-btn" onclick="startShow()">▶ 开始轮播</button>
</div>

<div id="info"></div>
<div id="countdown"></div>
<div id="container"></div>
<div id="done">{done_text}</div>

<script>
// 这里存储的是相对路径，HTML 体积非常小
const imgPaths = {js_paths_array};

const DISPLAY_MS = {display_ms};
const FLASH_MS = {flash_ms};
const BATCH_SIZE = {batch_size};
const INFO_PREFIX = {json.dumps(info_prefix)};
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
  const container = document.getElementById('container');
  container.innerHTML = '';
  document.getElementById('info').textContent = '';

  setTimeout(function() {{
    let currentBatchCount = Math.min(BATCH_SIZE, imgPaths.length - currentIndex);

    // 根据本批实际数量动态铺满屏幕：尽量贴近原 cols x rows 比例，
    // 不足一屏时压缩行/列，使每张二维码尽可能放大。
    const COLS_FULL = {cols};
    const ROWS_FULL = {rows};
    let cols = Math.min(COLS_FULL, currentBatchCount);
    let rows = Math.ceil(currentBatchCount / cols);
    if (rows > ROWS_FULL) {{
      rows = ROWS_FULL;
      cols = Math.ceil(currentBatchCount / rows);
    }}
    container.style.gridTemplateColumns = 'repeat(' + cols + ', minmax(0, 1fr))';
    container.style.gridTemplateRows = 'repeat(' + rows + ', minmax(0, 1fr))';

    for(let i = 0; i < currentBatchCount; i++) {{
        let idx = currentIndex + i;
        let div = document.createElement('div');
        let img = document.createElement('img');
        // 核心：通过 src 引用外部文件，浏览器会高效处理内存
        img.src = imgPaths[idx];
        div.appendChild(img);
        container.appendChild(div);
    }}

    let startIdx = currentIndex + 1;
    let endIdx = currentIndex + currentBatchCount;
    document.getElementById('info').textContent =
      INFO_PREFIX + '[' + startIdx + '-' + endIdx + ' / ' + imgPaths.length + ']';

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

    out_name = output_name or default_out
    out_path = os.path.join(qr_output_dir, out_name)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"📄 轮播文件已生成: {out_path}")
    print(f"💡 提示：请确保 HTML 文件与图片目录保持相对位置不变。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SVG/PNG 矩阵轮播页面生成器（外部引用版，支持补丁模式）"
    )
    parser.add_argument("qr_dir", help="包含生成的二维码图片的根目录")
    parser.add_argument("interval_pos", nargs='?', type=int, default=None,
                        help="位置参数：每屏显示秒数（兼容旧用法）")
    parser.add_argument("cols_pos", nargs='?', type=int, default=None,
                        help="位置参数：列数（兼容旧用法）")
    parser.add_argument("rows_pos", nargs='?', type=int, default=None,
                        help="位置参数：行数（兼容旧用法）")
    parser.add_argument("-i", "--interval", type=int, default=None, help="每屏显示秒数 (默认: 5)")
    parser.add_argument("-c", "--cols", type=int, default=None, help="矩阵列数 (默认: 5)")
    parser.add_argument("-r", "--rows", type=int, default=None, help="矩阵行数 (默认: 3)")
    parser.add_argument("-f", "--file", default=None,
                        help="补丁模式：读取 decode_qr 导出的 missing_patches.txt 文件 "
                             "(格式: fname|1,2,3 或 fname|ALL)")
    parser.add_argument("-o", "--output", default=None,
                        help="输出 HTML 文件名 (默认: 普通模式 slideshow.html，补丁模式 patch_slideshow.html)")

    args = parser.parse_args()

    if not os.path.isdir(args.qr_dir):
        print(f"❌ 目录不存在: {args.qr_dir}")
        sys.exit(1)

    # 兼容旧位置参数：后到先到，命名参数优先
    interval = args.interval if args.interval is not None else (args.interval_pos if args.interval_pos is not None else 5)
    cols = args.cols if args.cols is not None else (args.cols_pos if args.cols_pos is not None else 5)
    rows = args.rows if args.rows is not None else (args.rows_pos if args.rows_pos is not None else 3)

    if args.file and not os.path.exists(args.file):
        print(f"❌ 报告文件不存在: {args.file}")
        sys.exit(1)

    generate_slideshow(
        args.qr_dir,
        interval=interval,
        cols=cols,
        rows=rows,
        missing_file=args.file,
        output_name=args.output,
    )

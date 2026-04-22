#!/usr/bin/env python3
"""
QR 码生成工具 v4 — 并行渲染 + Base32/Alphanumeric 载荷

相对 v3 的变化：
  1. 载荷格式 v2：V2:FNAMEB32:IDX/TOTAL:TAG:DATAB32
     - 全部字符在 QR alphanumeric 合法集 (0-9 A-Z [space] $%*+-./:) 内
     - 单码容量较 byte 模式提升 ~60%，总片数约减 25%
  2. 分片渲染使用 multiprocessing.Pool 并行，CLI 新增 -jN
  3. 仅服务新格式；历史 v1 二维码请继续用 v3 流水线
"""

import pyqrcode
import zlib
import lzma
import base64
import os
import sys
import math
import re
import tempfile
import multiprocessing


def strip_verilog(text):
    """Verilog 文本预处理：去注释、压缩空白"""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r'//.*', '', text)
    lines = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped:
            leading = len(line) - len(line.lstrip())
            if leading > 0:
                indent_level = leading // 2 if leading < 8 else leading // 4
                indent_level = max(indent_level, 1)
                stripped = ' ' * indent_level + line.lstrip()
            lines.append(stripped)
        else:
            if lines and lines[-1] != '':
                lines.append('')
    return '\n'.join(lines).strip() + '\n'


def strip_generic(text):
    """通用文本预处理：去末尾空白、合并空行"""
    lines = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped:
            lines.append(stripped)
        else:
            if lines and lines[-1] != '':
                lines.append('')
    return '\n'.join(lines).strip() + '\n'


def compress_data(raw_bytes, method='lzma'):
    """压缩数据，返回 (压缩后字节, 方法标识)"""
    if method == 'lzma':
        compressed = lzma.compress(raw_bytes, preset=9)
        tag = 'X'
    else:
        compressed = zlib.compress(raw_bytes, level=9)
        tag = 'Z'
    return compressed, tag


def _b32_strip(raw: bytes) -> str:
    """Base32 编码，去掉 '=' padding（QR alphanumeric 模式不允许 '='）"""
    return base64.b32encode(raw).decode('ascii').rstrip('=')


def _render_qr_chunk(task):
    """Worker（模块级、可 pickle）：单片 QR 渲染 + SVG 落盘。

    返回 (idx, ok, err_msg_or_None)。
    """
    idx, qr_payload, svg_path, error_level, scale = task
    try:
        qr = pyqrcode.create(qr_payload, error=error_level, mode='alphanumeric')
        qr.svg(svg_path, scale=scale, background="white", module_color="black")
        return (idx, True, None)
    except Exception as e:
        return (idx, False, f"{type(e).__name__}: {e}")


def generate_svg_qr_chunked(file_path, out_dir, error_level='L', scale=10,
                             chunk_size=None, strip=False, compress_method='auto',
                             qr_name=None, num_workers=1):
    """
    将单个文件压缩后拆分为多个 QR 码 SVG 文件（v2 格式）。

    分片格式: V2:FNAMEB32:IDX/TOTAL:TAG:DATAB32
    qr_name:  写入 QR 载荷的文件标识（可含路径如 subdir/file.v）
    num_workers: 并行渲染进程数；<=1 走串行

    返回: 成功返回生成的二维码数量，失败返回 -1，跳过返回 0
    """

    # ================================================================
    # 容量设置（alphanumeric 模式，VNC 友好，约 V15–V20）
    # 参照 QR 规格在 alphanumeric 模式下的字符容量，相对 v3 byte 模式 +60%
    # ================================================================
    MAX_CAPACITY = {
        'L': 1300,
        'M': 1050,
        'Q':  730,
        'H':  570,
    }
    ERROR_PERCENT = {'L': '7%', 'M': '15%', 'Q': '25%', 'H': '30%'}

    if chunk_size is None:
        chunk_size = MAX_CAPACITY.get(error_level, 570)

    fname = qr_name if qr_name else os.path.basename(file_path)

    print(f"  ⚙️  正在读取: {file_path}")

    # 跳过不存在的软链接目标
    if os.path.islink(file_path) and not os.path.exists(file_path):
        print(f"  ⚠️  软链接目标不存在，跳过: {file_path}")
        return 0

    with open(file_path, 'rb') as f:
        raw = f.read()

    original_size = len(raw)

    if original_size == 0:
        print(f"  ⚠️  空白文件，跳过")
        return 0
    print(f"  📄 原始大小: {original_size:,} 字节 ({original_size/1024:.1f} KB)")

    # ---- 可选：文本预处理 ----
    if strip:
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            print("  ⚠️  文件非 UTF-8 文本，跳过预处理")
            strip = False

    if strip:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in ('.v', '.sv', '.vh', '.svh'):
            text = strip_verilog(text)
            print(f"  🧹 Verilog 预处理: 去注释 + 压缩空白")
        else:
            text = strip_generic(text)
            print(f"  🧹 通用预处理: 去末尾空白 + 合并空行")
        raw = text.encode('utf-8')
        stripped_size = len(raw)
        saved = original_size - stripped_size
        print(f"     预处理后: {stripped_size:,} 字节 (节省 {saved:,} 字节, {saved/original_size*100:.1f}%)")

    # ---- 压缩 ----
    if compress_method == 'auto':
        comp_lzma, _ = compress_data(raw, 'lzma')
        comp_zlib, _ = compress_data(raw, 'zlib')
        if len(comp_lzma) <= len(comp_zlib):
            compressed, tag = comp_lzma, 'X'
            chosen = 'lzma'
        else:
            compressed, tag = comp_zlib, 'Z'
            chosen = 'zlib'
        print(f"  🗜️  压缩对比: lzma={len(comp_lzma):,}  zlib={len(comp_zlib):,}  → 选用 {chosen}")
    else:
        compressed, tag = compress_data(raw, compress_method)
        chosen = compress_method

    compressed_size = len(compressed)
    ratio = compressed_size / original_size * 100
    print(f"  🗜️  压缩后:   {compressed_size:,} 字节 ({compressed_size/1024:.1f} KB, {ratio:.1f}%)")

    # ---- Base32 编码（去 '=' padding）----
    data_b32 = _b32_strip(compressed)
    payload = data_b32
    payload_len = len(payload)
    print(f"  📦 Base32:   {payload_len:,} 字符")

    # 文件名也用 base32 编码（UTF-8 → base32），兼容含小写/下划线/中文的路径
    fname_b32 = _b32_strip(fname.encode('utf-8'))

    # ---- 分片 ----
    # 固定 header: "V2:" + fname_b32 + ":" + "IDX/TOTAL" + ":" + TAG + ":"
    # IDX/TOTAL 最多按 8 位十进制保留；再加 4 字符安全余量
    header_reserve = 3 + len(fname_b32) + 1 + 8 + 1 + 1 + 1 + 4
    effective_chunk = chunk_size - header_reserve
    if effective_chunk <= 0:
        print(f"  ❌ 文件标识太长（base32 后 {len(fname_b32)} 字符），每片可用容量不足")
        return -1

    total_chunks = math.ceil(payload_len / effective_chunk)
    print(f"  🔢 纠错等级: {error_level} ({ERROR_PERCENT[error_level]})")
    print(f"  📊 每片容量: {effective_chunk} 字符  |  共 {total_chunks} 个二维码")

    os.makedirs(out_dir, exist_ok=True)

    # ---- 构造任务列表 ----
    tasks = []
    for i in range(total_chunks):
        start = i * effective_chunk
        end = start + effective_chunk
        chunk_data = payload[start:end]
        qr_payload = f"V2:{fname_b32}:{i+1}/{total_chunks}:{tag}:{chunk_data}"
        svg_path = os.path.join(out_dir, f"part_{i+1:03d}.svg")
        tasks.append((i + 1, qr_payload, svg_path, error_level, scale))

    # ---- 串行 / 并行渲染 ----
    if num_workers <= 1:
        results = []
        for t in tasks:
            r = _render_qr_chunk(t)
            results.append(r)
            idx = r[0]
            print(f"\r    ⏳ {idx}/{total_chunks} 已生成", end="", flush=True)
            if not r[1]:
                break
        print()
    else:
        chunksize = max(1, total_chunks // (num_workers * 4))
        results = []
        done = 0
        with multiprocessing.Pool(processes=num_workers) as pool:
            for r in pool.imap_unordered(_render_qr_chunk, tasks, chunksize=chunksize):
                done += 1
                results.append(r)
                print(f"\r    ⏳ {done}/{total_chunks} 已生成 (并行 {num_workers})",
                      end="", flush=True)
        print()

    errors = [r for r in results if not r[1]]
    if errors:
        idx0, _, err0 = errors[0]
        print(f"  ❌ 第 {idx0} 片生成失败: {err0}（共 {len(errors)} 片失败）")
        return -1

    print(f"  ✨ 完成！共 {total_chunks} 个 SVG → {out_dir}/")
    return total_chunks


def process_single_file(file_path, error_level='L', strip=False, num_workers=1):
    """处理单个文件，生成二维码到独立输出目录。"""

    if not os.path.isfile(file_path):
        print(f"❌ 文件不存在: {file_path}")
        sys.exit(1)

    fname = os.path.basename(file_path)
    name_no_ext = os.path.splitext(fname)[0]
    out_dir = f"qr_output_{name_no_ext}"

    print(f"📄 输入文件: {file_path}")
    print(f"📁 输出目录: {out_dir}/")
    print(f"🔢 纠错等级: {error_level}")
    print(f"⚡ 并行进程: {num_workers}")
    if strip:
        print(f"🧹 文本预处理: 开启")
    print()

    print("=" * 55)
    print(f"📄 {fname}")
    print("-" * 55)

    count = generate_svg_qr_chunked(
        file_path=file_path,
        out_dir=out_dir,
        error_level=error_level,
        strip=strip,
        num_workers=num_workers,
    )

    success_files = 1 if count > 0 else 0

    report_lines = [
        "",
        "=" * 55,
        "📊 汇总报告",
        "=" * 55
    ]
    if count > 0:
        report_lines.append(f"  ✅ {fname}: {count} 个二维码")
    elif count == 0:
        report_lines.append(f"  ⏭️  {fname}: 已跳过")
    else:
        report_lines.append(f"  ❌ {fname}: 生成失败")

    report_lines.extend([
        "-" * 55,
        f"  📁 输出目录: {out_dir}/",
        f"  📄 成功处理文件数: {success_files} / 1",
        f"  🔢 二维码总数: {max(count, 0)}",
        "=" * 55
    ])

    report_text = "\n".join(report_lines)
    print(report_text)

    log_file = os.path.join(out_dir, "generation_record.txt")
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(report_text)
    print(f"  📝 记录文件已保存至: {log_file}")


def process_directory(input_dir, error_level='L', strip=False, num_workers=1):
    """遍历文件夹下所有文件，每个文件生成独立的二维码子目录。"""

    if not os.path.isdir(input_dir):
        print(f"❌ 目录不存在: {input_dir}")
        sys.exit(1)

    all_files = []
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in sorted(files):
            if not f.startswith('.'):
                all_files.append(os.path.join(root, f))
    all_files.sort()

    if not all_files:
        print(f"❌ 文件夹为空: {input_dir}")
        sys.exit(1)

    dir_name = os.path.basename(os.path.normpath(input_dir))
    root_out = f"qr_output_{dir_name}"
    os.makedirs(root_out, exist_ok=True)

    print(f"📂 输入目录: {input_dir}")
    print(f"📁 输出目录: {root_out}/")
    print(f"📋 共 {len(all_files)} 个文件待处理")
    print(f"🔢 纠错等级: {error_level}")
    print(f"⚡ 并行进程: {num_workers}")
    if strip:
        print(f"🧹 文本预处理: 开启")
    print()

    results = []
    total_qr = 0

    for i, fpath in enumerate(all_files, 1):
        rel_path = os.path.relpath(fpath, input_dir)

        print("=" * 55)
        print(f"[{i}/{len(all_files)}] 📄 {rel_path}")
        print("-" * 55)

        sub_dir = os.path.join(root_out, rel_path)

        count = generate_svg_qr_chunked(
            file_path=fpath,
            out_dir=sub_dir,
            error_level=error_level,
            strip=strip,
            qr_name=rel_path,
            num_workers=num_workers,
        )

        if count > 0:
            results.append((rel_path, count))
            total_qr += count
        elif count == 0:
            results.append((rel_path, 'SKIPPED'))
        else:
            results.append((rel_path, 'FAILED'))

        print()

    # ==========================================
    # 生成全局文件清单 (Manifest) 并二维码化
    # 源 txt 写入 tempfile，避免源文件路径与 QR 输出目录同路径
    # ==========================================
    manifest_name = "_manifest.txt"
    successful_files_list = [rel_path for rel_path, count in results if count not in ('FAILED', 'SKIPPED')]

    if successful_files_list:
        print("=" * 55)
        print("📝 生成全局文件清单 (Manifest)")
        print("-" * 55)

        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix="_manifest.txt", delete=False
        ) as tf:
            tf.write("\n".join(successful_files_list))
            manifest_src = tf.name
        try:
            manifest_count = generate_svg_qr_chunked(
                file_path=manifest_src,
                out_dir=os.path.join(root_out, manifest_name),
                error_level=error_level,
                strip=False,
                qr_name=manifest_name,
                num_workers=num_workers,
            )
        finally:
            os.unlink(manifest_src)

        if manifest_count > 0:
            results.append((manifest_name, manifest_count))
            total_qr += manifest_count
            print(f"  ✅ 清单文件已生成: {manifest_count} 个二维码")
        else:
            print(f"  ❌ 清单文件生成失败")
        print()

        # 文件计数哨兵
        count_name = "_manifest_count.txt"
        file_count = len(successful_files_list)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix="_manifest_count.txt", delete=False
        ) as tf:
            tf.write(f"N={file_count}")
            count_src = tf.name
        try:
            count_qr = generate_svg_qr_chunked(
                file_path=count_src,
                out_dir=os.path.join(root_out, count_name),
                error_level=error_level,
                strip=False,
                qr_name=count_name,
                num_workers=num_workers,
            )
        finally:
            os.unlink(count_src)

        if count_qr > 0:
            results.append((count_name, count_qr))
            total_qr += count_qr
            print(f"  ✅ 文件计数哨兵已生成: {count_qr} 个二维码 (N={file_count})")
        else:
            print(f"  ❌ 文件计数哨兵生成失败")
        print()

    # 汇总报告
    successful_files = 0
    report_lines = [
        "=" * 55,
        "📊 汇总报告",
        "=" * 55
    ]

    for fname, count in results:
        if count == 'FAILED':
            report_lines.append(f"  ❌ {fname}: 生成失败")
        elif count == 'SKIPPED':
            report_lines.append(f"  ⏭️  {fname}: 已跳过")
        else:
            report_lines.append(f"  ✅ {fname}: {count} 个二维码")
            successful_files += 1

    report_lines.extend([
        "-" * 55,
        f"  📁 总输出目录: {root_out}/",
        f"  📄 成功处理文件数: {successful_files} / {len(all_files)}",
        f"  🔢 二维码总数: {total_qr}",
        "=" * 55
    ])

    report_text = "\n".join(report_lines)
    print(report_text)

    log_file = os.path.join(root_out, "generation_record.txt")
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(report_text)
    print(f"  📝 记录文件已保存至: {log_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 gen_terminal_qr_v4.py <文件或文件夹路径> [选项]")
        print()
        print("选项:")
        print("  L/M/Q/H        纠错等级 (默认 L)")
        print("  --strip        文本预处理: 去注释、压缩空白")
        print("  -jN            并行进程数 (默认 = CPU 核数)")
        print()
        print("示例:")
        print("  python3 gen_terminal_qr_v4.py ./my_designs/")
        print("  python3 gen_terminal_qr_v4.py ./my_designs/ M -j8")
        print("  python3 gen_terminal_qr_v4.py ./my_designs/ L --strip -j4")
        print("  python3 gen_terminal_qr_v4.py ./single_file.v")
        print("  python3 gen_terminal_qr_v4.py ./single_file.v M --strip -j8")
        print()
        print("二维码分片格式 (v2): V2:FNAMEB32:IDX/TOTAL:TAG:DATAB32")
        print("  FNAMEB32: 相对路径 UTF-8 → base32 (去 '=' padding)")
        print("  TAG:      压缩方法 X=lzma Z=zlib")
        print("  DATAB32:  压缩后字节流 → base32")
        print("  全部字符位于 QR alphanumeric 合法集，单码容量较 byte 模式 +60%")
        print()
        print("纠错等级 (VNC 友好模式，每片约 V15-V20):")
        print("  L = 7%   每片 ~1300 字符")
        print("  M = 15%  每片 ~1050 字符")
        print("  Q = 25%  每片 ~730 字符")
        print("  H = 30%  每片 ~570 字符")
        sys.exit(1)

    input_path = None
    error_level = 'L'
    strip = False
    num_workers = multiprocessing.cpu_count()

    args = sys.argv[1:]
    for arg in args:
        if arg.startswith('-j'):
            try:
                n = int(arg[2:])
                num_workers = max(1, n)
            except ValueError:
                print(f"⚠️  无效的进程数: {arg}，使用默认值 {num_workers}")
        elif arg.upper() in ('L', 'M', 'Q', 'H'):
            error_level = arg.upper()
        elif arg == '--strip':
            strip = True
        elif input_path is None:
            input_path = arg
        else:
            print(f"⚠️  未知参数: {arg}")

    if input_path is None:
        print("❌ 未提供输入文件/目录路径")
        sys.exit(1)

    if os.path.isfile(input_path):
        process_single_file(input_path, error_level=error_level, strip=strip,
                             num_workers=num_workers)
    else:
        process_directory(input_path, error_level=error_level, strip=strip,
                          num_workers=num_workers)

import pyqrcode
import zlib
import lzma
import base64
import os
import sys
import math
import re


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


def generate_svg_qr_chunked(file_path, out_dir, error_level='L', scale=10,
                             chunk_size=None, strip=False, compress_method='auto',
                             qr_name=None):
    """
    将单个文件压缩后拆分为多个 QR 码 SVG 文件。

    分片格式: 路径名|序号/总数|数据

    qr_name: 写入 QR 载荷的文件标识（可含路径如 subdir/file.v），
             默认为 os.path.basename(file_path)。

    返回: 成功返回生成的二维码数量，失败返回 -1
    """

    # ================================================================
    # 容量设置（VNC 友好模式）
    # ================================================================
    MAX_CAPACITY = {
        'L': 800,
        'M': 650,
        'Q': 450,
        'H': 350,
    }
    ERROR_PERCENT = {'L': '7%', 'M': '15%', 'Q': '25%', 'H': '30%'}

    if chunk_size is None:
        chunk_size = MAX_CAPACITY.get(error_level, 350)

    # 使用传入的 qr_name（含目录层级），否则退回到 basename
    fname = qr_name if qr_name else os.path.basename(file_path)

    print(f"  ⚙️  正在读取: {file_path}")

    # 跳过不存在的软链接目标
    if os.path.islink(file_path) and not os.path.exists(file_path):
        print(f"  ⚠️  软链接目标不存在，跳过: {file_path}")
        return 0

    with open(file_path, 'rb') as f:
        raw = f.read()

    original_size = len(raw)

    # 跳过空白文件
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

    # ---- Base64 编码 ----
    payload = tag + base64.b64encode(compressed).decode('ascii')
    payload_len = len(payload)
    print(f"  📦 Base64:   {payload_len:,} 字符")

    # ---- 分片 ----
    header_reserve = len(fname) + 12
    effective_chunk = chunk_size - header_reserve
    if effective_chunk <= 0:
        print(f"  ❌ 文件标识太长({len(fname)}字符)，导致每片可用容量不足")
        return -1

    total_chunks = math.ceil(payload_len / effective_chunk)
    print(f"  🔢 纠错等级: {error_level} ({ERROR_PERCENT[error_level]})")
    print(f"  📊 每片容量: {effective_chunk} 字符  |  共 {total_chunks} 个二维码")

    os.makedirs(out_dir, exist_ok=True)

    # ---- 逐片生成 QR 码 ----
    for i in range(total_chunks):
        start = i * effective_chunk
        end = start + effective_chunk
        chunk_data = payload[start:end]

        # 分片格式: 路径名|序号/总数|数据
        qr_payload = f"{fname}|{i+1}/{total_chunks}|{chunk_data}"

        print(f"    ⏳ [{i+1:>3}/{total_chunks}] 生成中...", end="", flush=True)
        try:
            qr = pyqrcode.create(qr_payload, error=error_level)
        except Exception as e:
            print(f"\n  ❌ 第 {i+1} 片生成失败: {e}")
            print(f"     数据长度: {len(qr_payload)} 字符")
            return -1

        svg_path = os.path.join(out_dir, f"part_{i+1:03d}.svg")
        qr.svg(svg_path, scale=scale, background="white", module_color="black")
        print(f" ✅")

    print(f"  ✨ 完成！共 {total_chunks} 个 SVG → {out_dir}/")
    return total_chunks


def process_directory(input_dir, error_level='L', strip=False):
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
            qr_name=rel_path,  # 关键：将相对路径写入 QR 载荷
        )

        if count > 0:
            results.append((rel_path, count))
            total_qr += count
        elif count == 0:
            results.append((rel_path, 'SKIPPED'))
        else:
            results.append((rel_path, 'FAILED'))

        print()

    # 汇总报告
    print("=" * 55)
    print("📊 汇总报告")
    print("=" * 55)
    for fname, count in results:
        if count == 'FAILED':
            print(f"  ❌ {fname}: 生成失败")
        elif count == 'SKIPPED':
            print(f"  ⏭️  {fname}: 已跳过")
        else:
            print(f"  ✅ {fname}: {count} 个二维码")
    print("-" * 55)
    print(f"  📁 总输出目录: {root_out}/")
    print(f"  🔢 二维码总数: {total_qr}")
    print("=" * 55)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python gen_terminal_qr_v2.py <文件夹路径> [选项]")
        print()
        print("选项:")
        print("  L/M/Q/H       纠错等级 (默认 L)")
        print("  --strip        文本预处理: 去注释、压缩空白")
        print()
        print("示例:")
        print("  python gen_terminal_qr_v2.py ./my_designs/")
        print("  python gen_terminal_qr_v2.py ./my_designs/ M")
        print("  python gen_terminal_qr_v2.py ./my_designs/ L --strip")
        print()
        print("二维码分片格式: 相对路径|序号/总数|数据")
        print("  路径中保留目录层级（如 subdir/file.v），解码端可还原目录结构")
        print()
        print("纠错等级 (VNC 友好模式，每片约 V15-V20):")
        print("  L = 7%   每片 ~800 字符")
        print("  M = 15%  每片 ~650 字符")
        print("  Q = 25%  每片 ~450 字符")
        print("  H = 30%  每片 ~350 字符")
        sys.exit(1)

    input_dir = sys.argv[1]
    error_level = 'L'
    strip = False

    for arg in sys.argv[2:]:
        if arg.upper() in ('L', 'M', 'Q', 'H'):
            error_level = arg.upper()
        elif arg == '--strip':
            strip = True
        else:
            print(f"⚠️  未知参数: {arg}")

    process_directory(input_dir, error_level=error_level, strip=strip)

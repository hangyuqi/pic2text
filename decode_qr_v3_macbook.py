#!/usr/bin/env python3
"""
QR 码解码拼接工具 v3（Mac 端）- 多引擎识别 + 多进程加速

将所有截图一股脑丢进来（支持嵌套子目录），自动按文件名分组、排序、拼接、解压，还原所有原始文件。

依赖安装（Mac 上执行）：
    pip3 install opencv-python pillow

可选（提升识别率）：
    brew install zbar          # 或 apt install libzbar0
    pip3 install pyzbar

用法：
    python3 decode_qr_v3.py <截图目录> [输出目录]
    python3 decode_qr_v3.py <截图目录> [输出目录] -j4    # 指定4进程

示例：
    python3 decode_qr_v3.py ./screenshots/
    python3 decode_qr_v3.py ./screenshots/ ./restored/
    python3 decode_qr_v3.py ./screenshots/ ./restored/ -j8

说明：
    二维码分片格式: 相对路径|序号/总数|数据
    脚本自动按路径分组，还原出多个独立文件，并保留原始目录层级。

识别引擎优先级：
    1. pyzbar（如果已安装） — 识别率最高
    2. libzbar ctypes 直连（如果系统有 libzbar） — 等效于 pyzbar
    3. OpenCV QRCodeDetectorAruco — 比原生 QRCodeDetector 更好
    4. OpenCV QRCodeDetector + 多策略预处理 — 兜底方案

性能优化：
    - 多进程并行扫描（默认 CPU 核心数）
    - zbar 路径下直接用灰度图，跳过不必要的 RGB 转换
    - OpenCV 仅在 zbar 不可用时才加载
"""

import os
import sys
import base64
import zlib
import lzma
import ctypes
import ctypes.util
import time
import multiprocessing
from functools import partial

from PIL import Image


IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff'}

# ============================================================
# 引擎探测（轻量，仅判断可用性，不初始化重对象）
# ============================================================

def _detect_engine():
    """
    探测可用的最佳引擎，返回引擎名字符串：
    'pyzbar' / 'ctypes_zbar' / 'opencv'
    """
    # 1. pyzbar
    try:
        from pyzbar.pyzbar import decode as _test
        return 'pyzbar'
    except ImportError:
        pass

    # 2. ctypes zbar
    lib_path = ctypes.util.find_library('zbar')
    if lib_path:
        try:
            ctypes.cdll.LoadLibrary(lib_path)
            return 'ctypes_zbar'
        except OSError:
            pass
    for name in ['libzbar.so.0', 'libzbar.so', 'libzbar.dylib', 'libzbar-0.dll']:
        try:
            ctypes.cdll.LoadLibrary(name)
            return 'ctypes_zbar'
        except OSError:
            continue

    # 3. OpenCV
    return 'opencv'


def _engine_display_name(engine):
    if engine == 'pyzbar':
        return 'pyzbar'
    elif engine == 'ctypes_zbar':
        return 'libzbar (ctypes 直连)'
    else:
        try:
            import cv2
            return f'OpenCV {cv2.__version__}'
        except ImportError:
            return 'OpenCV'


# ============================================================
# Worker 进程：每个进程初始化自己的解码器，然后处理分配到的图片
# ============================================================

# 进程级全局状态（由 _worker_init 初始化）
_w_engine = None
_w_zbar_lib = None
_w_zbar_scanner = None


def _worker_init(engine_name):
    """在每个 worker 进程启动时调用，初始化解码器"""
    global _w_engine, _w_zbar_lib, _w_zbar_scanner
    _w_engine = engine_name

    if engine_name == 'ctypes_zbar':
        _w_zbar_lib, _w_zbar_scanner = _init_ctypes_zbar()
    # pyzbar 和 opencv 不需要进程级初始化


def _init_ctypes_zbar():
    """初始化 ctypes zbar 后端，返回 (lib, scanner)"""
    lib_path = ctypes.util.find_library('zbar')
    zbar_lib = None
    if lib_path:
        try:
            zbar_lib = ctypes.cdll.LoadLibrary(lib_path)
        except OSError:
            pass
    if not zbar_lib:
        for name in ['libzbar.so.0', 'libzbar.so', 'libzbar.dylib', 'libzbar-0.dll']:
            try:
                zbar_lib = ctypes.cdll.LoadLibrary(name)
                break
            except OSError:
                continue

    if not zbar_lib:
        return None, None

    zbar_lib.zbar_image_scanner_create.restype = ctypes.c_void_p
    zbar_lib.zbar_image_create.restype = ctypes.c_void_p
    zbar_lib.zbar_image_scanner_set_config.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int
    ]
    zbar_lib.zbar_image_set_format.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    zbar_lib.zbar_image_set_size.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    zbar_lib.zbar_image_set_data.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p
    ]
    zbar_lib.zbar_scan_image.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    zbar_lib.zbar_scan_image.restype = ctypes.c_int
    zbar_lib.zbar_image_first_symbol.argtypes = [ctypes.c_void_p]
    zbar_lib.zbar_image_first_symbol.restype = ctypes.c_void_p
    zbar_lib.zbar_symbol_next.argtypes = [ctypes.c_void_p]
    zbar_lib.zbar_symbol_next.restype = ctypes.c_void_p
    zbar_lib.zbar_symbol_get_data.argtypes = [ctypes.c_void_p]
    zbar_lib.zbar_symbol_get_data.restype = ctypes.c_char_p
    zbar_lib.zbar_symbol_get_data_length.argtypes = [ctypes.c_void_p]
    zbar_lib.zbar_symbol_get_data_length.restype = ctypes.c_uint
    zbar_lib.zbar_image_destroy.argtypes = [ctypes.c_void_p]
    zbar_lib.zbar_image_scanner_destroy.argtypes = [ctypes.c_void_p]

    scanner = zbar_lib.zbar_image_scanner_create()
    zbar_lib.zbar_image_scanner_set_config(scanner, 0, 0, 1)

    return zbar_lib, scanner


def _scan_one_image(image_path):
    """
    Worker 函数：扫描单张图片，返回 (image_path, [decoded_texts])。
    在子进程中执行，使用进程级全局解码器。
    """
    try:
        results = []

        if _w_engine == 'pyzbar':
            results = _decode_pyzbar(image_path)
        elif _w_engine == 'ctypes_zbar':
            results = _decode_ctypes_zbar(image_path)

        if not results:
            results = _decode_opencv(image_path)

        return (image_path, results)
    except Exception as e:
        return (image_path, [])


def _decode_pyzbar(image_path):
    """pyzbar 解码 — 直接用灰度图"""
    from pyzbar.pyzbar import decode as pyzbar_decode
    img = Image.open(image_path).convert('L')
    results = pyzbar_decode(img)
    return [r.data.decode('utf-8') for r in results if r.data]


def _decode_ctypes_zbar(image_path):
    """ctypes zbar 解码 — 直接用灰度图，零拷贝"""
    gray = Image.open(image_path).convert('L')
    w, h = gray.size
    raw_data = gray.tobytes()

    zimg = _w_zbar_lib.zbar_image_create()
    _w_zbar_lib.zbar_image_set_format(zimg, 0x30303859)  # Y800
    _w_zbar_lib.zbar_image_set_size(zimg, w, h)
    _w_zbar_lib.zbar_image_set_data(zimg, raw_data, len(raw_data), None)
    _w_zbar_lib.zbar_scan_image(_w_zbar_scanner, zimg)

    results = []
    sym = _w_zbar_lib.zbar_image_first_symbol(zimg)
    while sym:
        data = _w_zbar_lib.zbar_symbol_get_data(sym)
        if data:
            try:
                results.append(data.decode('utf-8'))
            except UnicodeDecodeError:
                pass
        sym = _w_zbar_lib.zbar_symbol_next(sym)

    _w_zbar_lib.zbar_image_destroy(zimg)
    return results


def _decode_opencv(image_path):
    """OpenCV 解码 — 多检测器 + 多预处理，仅在 zbar 失败时调用"""
    import cv2
    import numpy as np

    try:
        # 直接用 cv2 读取，比 PIL→numpy 转换更快
        img_bgr = cv2.imread(image_path)
        if img_bgr is None:
            # 回退到 PIL（处理 cv2 不支持的路径编码）
            pil_img = Image.open(image_path).convert('RGB')
            img_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    except Exception:
        return []

    # 检测器列表
    detectors = []
    try:
        detectors.append(cv2.QRCodeDetectorAruco())
    except Exception:
        pass
    detectors.append(cv2.QRCodeDetector())

    # 图像变体（懒生成，找到即停）
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]

    def _variants():
        yield img_bgr

        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        yield cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

        block_size = max(11, (min(h, w) // 20) | 1)
        adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                         cv2.THRESH_BINARY, block_size, 2)
        yield cv2.cvtColor(adaptive, cv2.COLOR_GRAY2BGR)

        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
        yield cv2.filter2D(img_bgr, -1, kernel)

        if max(h, w) < 1500:
            yield cv2.resize(img_bgr, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        yield cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2BGR)

    for detector in detectors:
        for var_img in _variants():
            results = []
            try:
                retval, decoded_list, pts, st = detector.detectAndDecodeMulti(var_img)
                if retval and decoded_list:
                    results = [t for t in decoded_list if t]
            except Exception:
                pass
            if not results:
                try:
                    data, pts, _ = detector.detectAndDecode(var_img)
                    if data:
                        results = [data]
                except Exception:
                    pass
            if results:
                return results

    return []


# ============================================================
# 文件收集与解析（与 v2 一致）
# ============================================================

def collect_images(scan_dir):
    """递归收集截图目录下所有图片文件，按路径排序。"""
    image_files = []
    for root, dirs, files in os.walk(scan_dir):
        dirs.sort()
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                image_files.append(os.path.join(root, f))
    image_files.sort()
    return image_files


def parse_qr_text(text):
    """
    解析二维码文本。
    新格式: 文件名|序号/总数|数据
    旧格式: 序号/总数|数据
    """
    parts = text.split('|')

    if len(parts) == 3:
        fname, header, data = parts
    elif len(parts) == 2:
        fname = 'output'
        header, data = parts
    else:
        return None

    try:
        idx, tot = header.split('/')
        idx, tot = int(idx), int(tot)
    except ValueError:
        return None

    return (fname, idx, tot, data)


def reassemble_file(fname, chunks, total, output_dir):
    """将单个文件的所有分片拼接、解压、写入"""

    missing = [i for i in range(1, total + 1) if i not in chunks]
    if missing:
        if len(missing) <= 20:
            print(f"  ❌ 缺少片段: {missing}")
        else:
            print(f"  ❌ 缺少 {len(missing)} 个片段: {missing[:10]}...{missing[-5:]}")
        return False

    payload = ''.join(chunks[i] for i in range(1, total + 1))

    compress_tag = payload[0]
    b64_data = payload[1:]

    if compress_tag == 'X':
        decompress_func = lzma.decompress
        compress_name = 'lzma'
    elif compress_tag == 'Z':
        decompress_func = zlib.decompress
        compress_name = 'zlib'
    else:
        b64_data = payload
        decompress_func = zlib.decompress
        compress_name = 'zlib (兼容旧版)'

    try:
        compressed = base64.b64decode(b64_data)
    except Exception as e:
        print(f"  ❌ Base64 解码失败: {e}")
        return False

    try:
        raw = decompress_func(compressed)
    except Exception as e:
        print(f"  ❌ 解压缩失败 ({compress_name}): {e}")
        print(f"     可能有片段数据损坏，请检查截图质量")
        return False

    out_path = os.path.join(output_dir, fname)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'wb') as f:
        f.write(raw)

    print(f"  ✅ 还原成功: {out_path} ({len(raw):,} 字节, {compress_name})")
    return True


# ============================================================
# 主流程（多进程版本）
# ============================================================

def decode_all(scan_dir, output_dir, num_workers):
    """主流程：多进程扫描 → 按文件名分组 → 逐个还原"""

    engine = _detect_engine()

    image_files = collect_images(scan_dir)
    if not image_files:
        print(f"❌ 在 {scan_dir} 下没有找到图片文件")
        sys.exit(1)

    total_images = len(image_files)
    print(f"📂 截图目录: {scan_dir}")
    print(f"📁 输出目录: {output_dir}")
    print(f"🖼️  找到 {total_images} 张截图（含子目录）")
    print(f"🔧 识别引擎: {_engine_display_name(engine)}")
    print(f"⚡ 并行进程: {num_workers}")
    print()

    # ---------- 多进程扫描 ----------
    t0 = time.time()

    file_groups = {}
    scanned_count = 0
    white_frames = 0
    failed_images = []
    done = 0

    if num_workers <= 1:
        # 单进程模式（调试用，或图片极少时）
        _worker_init(engine)
        results_iter = (_scan_one_image(f) for f in image_files)
    else:
        pool = multiprocessing.Pool(
            processes=num_workers,
            initializer=_worker_init,
            initargs=(engine,)
        )
        # imap_unordered: 谁先完成谁先返回，最大化吞吐
        results_iter = pool.imap_unordered(_scan_one_image, image_files, chunksize=4)

    try:
        for fpath, texts in results_iter:
            done += 1
            rel_name = os.path.relpath(fpath, scan_dir)

            if not texts:
                white_frames += 1
                # 进度条形式，不逐行打印跳过的图
                print(f"\r  ⏳ 进度: {done}/{total_images} (已识别 {scanned_count} 片)", end="", flush=True)
                continue

            recognized = 0
            last_fname = last_idx = last_tot = None
            for text in texts:
                parsed = parse_qr_text(text)
                if parsed is None:
                    continue

                fname, idx, tot, data = parsed
                last_fname, last_idx, last_tot = fname, idx, tot
                recognized += 1

                if fname not in file_groups:
                    file_groups[fname] = {'total': tot, 'chunks': {}}
                if idx not in file_groups[fname]['chunks']:
                    file_groups[fname]['chunks'][idx] = data

            if recognized > 0:
                scanned_count += recognized
                print(f"\r  ✅ [{done}/{total_images}] {last_fname} [{last_idx}/{last_tot}]" + " " * 20)
            else:
                failed_images.append(rel_name)

            print(f"\r  ⏳ 进度: {done}/{total_images} (已识别 {scanned_count} 片)", end="", flush=True)
    finally:
        if num_workers > 1:
            pool.close()
            pool.join()

    elapsed = time.time() - t0
    speed = total_images / elapsed if elapsed > 0 else 0

    print(f"\r  ✅ 扫描完成！{total_images} 张截图用时 {elapsed:.1f}s ({speed:.1f} 张/秒)" + " " * 20)

    # ---------- 汇总 ----------
    print()
    print("=" * 55)
    print("📊 识别汇总")
    print("=" * 55)
    print(f"  🖼️  截图总数:   {total_images}")
    print(f"  ✅ 有效片段:   {scanned_count}")
    print(f"  ⏭️  白屏/空帧:  {white_frames}")
    print(f"  📄 发现文件:   {len(file_groups)} 个")
    if failed_images:
        print(f"  ⚠️  格式异常:   {len(failed_images)} 张")

    print()
    for fname, info in sorted(file_groups.items()):
        got = len(info['chunks'])
        tot = info['total']
        status = "✅ 齐全" if got == tot else f"❌ 缺 {tot - got} 片"
        print(f"     {fname}: {got}/{tot} {status}")

    # ---------- 还原 ----------
    print()
    print("=" * 55)
    print("🔗 开始还原文件")
    print("=" * 55)

    success = 0
    fail = 0

    for fname in sorted(file_groups.keys()):
        info = file_groups[fname]
        tot = info['total']
        chunks = info['chunks']

        print(f"\n  📄 {fname} ({len(chunks)}/{tot} 片)")

        if reassemble_file(fname, chunks, tot, output_dir):
            success += 1
        else:
            fail += 1

    # ---------- 最终报告 ----------
    print()
    print("=" * 55)
    print("📊 最终结果")
    print("=" * 55)
    print(f"  ✅ 成功还原: {success} 个文件")
    if fail > 0:
        print(f"  ❌ 还原失败: {fail} 个文件")
    print(f"  📁 输出目录: {output_dir}")
    print(f"  ⏱️  总耗时:   {elapsed:.1f}s")
    print("=" * 55)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 decode_qr_v3.py <截图目录> [输出目录] [-jN]")
        print()
        print("示例:")
        print("  python3 decode_qr_v3.py ./screenshots/")
        print("  python3 decode_qr_v3.py ./screenshots/ ./restored/")
        print("  python3 decode_qr_v3.py ./screenshots/ ./restored/ -j4")
        print()
        print("选项:")
        print("  -jN   并行进程数，默认为 CPU 核心数")
        print("        例: -j4 表示4进程, -j1 表示单进程（调试用）")
        print()
        print("说明:")
        print("  把所有截图丢到一个文件夹里（支持子目录），脚本自动:")
        print("  1. 递归扫描所有子目录中的截图")
        print("  2. 多进程并行识别二维码（自动选择最佳引擎）")
        print("  3. 按文件名自动分组")
        print("  4. 检查每个文件的片段完整性")
        print("  5. 逐个还原为原始文件")
        print()
        print("依赖安装:")
        print("  pip3 install opencv-python pillow")
        print()
        print("可选（提升识别率）:")
        print("  brew install zbar       # macOS")
        print("  apt install libzbar0    # Linux")
        print("  pip3 install pyzbar")
        sys.exit(1)

    # 解析参数
    positional = []
    num_workers = multiprocessing.cpu_count()

    for arg in sys.argv[1:]:
        if arg.startswith('-j'):
            try:
                num_workers = int(arg[2:])
                if num_workers < 1:
                    num_workers = 1
            except ValueError:
                print(f"⚠️  无效的进程数: {arg}，使用默认值 {num_workers}")
        else:
            positional.append(arg)

    scan_dir = positional[0] if positional else '.'
    output_dir = positional[1] if len(positional) > 1 else './restored/'

    if not os.path.isdir(scan_dir):
        print(f"❌ 目录不存在: {scan_dir}")
        sys.exit(1)

    decode_all(scan_dir, output_dir, num_workers)

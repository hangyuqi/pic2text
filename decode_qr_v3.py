#!/usr/bin/env python3
"""
QR 码解码拼接工具 v3（Mac 端）- 多引擎识别 + 多进程加速 + 缺失报告生成

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
    from pyzbar.pyzbar import decode as pyzbar_decode
    img = Image.open(image_path).convert('L')
    results = pyzbar_decode(img)
    return [r.data.decode('utf-8') for r in results if r.data]

def _decode_ctypes_zbar(image_path):
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
    import cv2
    import numpy as np

    try:
        img_bgr = cv2.imread(image_path)
        if img_bgr is None:
            pil_img = Image.open(image_path).convert('RGB')
            img_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    except Exception:
        return []

    detectors = []
    try:
        detectors.append(cv2.QRCodeDetectorAruco())
    except Exception:
        pass
    detectors.append(cv2.QRCodeDetector())

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
# 文件收集、解析与实用工具
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

def format_missing_ranges(missing_list):
    """
    将缺失的片段列表格式化为连续区间形式以提高可读性。
    例如: [1, 2, 3, 5, 8, 9, 10] -> "1-3, 5, 8-10"
    """
    if not missing_list:
        return ""
    
    missing_list = sorted(missing_list)
    ranges = []
    start = missing_list[0]
    end = missing_list[0]
    
    for i in range(1, len(missing_list)):
        if missing_list[i] == end + 1:
            end = missing_list[i]
        else:
            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{end}")
            start = missing_list[i]
            end = missing_list[i]
            
    if start == end:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{end}")
        
    return ", ".join(ranges)

def reassemble_file(fname, chunks, total, output_dir):
    """将单个文件的所有分片拼接、解压、写入"""

    missing = [i for i in range(1, total + 1) if i not in chunks]
    if missing:
        # 使用新的范围格式化方法输出当前文件的缺失提示
        missing_str = format_missing_ranges(missing)
        print(f"  ❌ 缺少 {len(missing)} 个片段: [{missing_str}]")
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
        _worker_init(engine)
        results_iter = (_scan_one_image(f) for f in image_files)
    else:
        pool = multiprocessing.Pool(
            processes=num_workers,
            initializer=_worker_init,
            initargs=(engine,)
        )
        results_iter = pool.imap_unordered(_scan_one_image, image_files, chunksize=4)

    try:
        for fpath, texts in results_iter:
            done += 1
            rel_name = os.path.relpath(fpath, scan_dir)

            if not texts:
                white_frames += 1
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
    
    # 记录每个失败文件的具体缺失片段，用于最终报告
    missing_reports = {}

    for fname in sorted(file_groups.keys()):
        info = file_groups[fname]
        tot = info['total']
        chunks = info['chunks']

        missing = [i for i in range(1, tot + 1) if i not in chunks]
        if missing:
            missing_reports[fname] = missing

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
    
    if missing_reports:
        print("-" * 55)
        print("⚠️  丢失片段详细报告:")
        
        # 收集所有缺失的全局序号（用于输出机器读取文件）
        all_missing_indices = set()
        
        for fname, missing in missing_reports.items():
            missing_str = format_missing_ranges(missing)
            print(f"  📄 {fname}: 缺少 {len(missing)} 片 -> [{missing_str}]")
            all_missing_indices.update(missing)
            
        # 自动生成供 gen_patch_slideshow.py 读取的配置文件
        patch_file_path = os.path.join(output_dir, "missing_patches.txt")
        with open(patch_file_path, "w", encoding="utf-8") as pf:
            for fname, missing in missing_reports.items():
                # 将该文件的缺失序号转为逗号分隔的字符串
                missing_str = ",".join(str(x) for x in sorted(missing))
                # 写入格式: 文件名|1,2,3,4
                pf.write(f"{fname}|{missing_str}\n")
        print("-" * 55)
        print(f"  🤖 已生成自动补丁配置文件:\n     {patch_file_path}")

    # ==========================================
    # 新增逻辑：基于 Manifest 检查整文件丢失
    # ==========================================
    manifest_path = os.path.join(output_dir, "_manifest.txt")
    if os.path.exists(manifest_path):
        print("-" * 55)
        print("🔍 执行全局文件完整性校验 (Manifest)")
        try:
            with open(manifest_path, "r", encoding="utf-8") as mf:
                expected_files = set(line.strip() for line in mf if line.strip())
            
            # 实际发现的文件集合（剔除清单文件本身）
            actual_files = set(file_groups.keys())
            if "_manifest.txt" in actual_files:
                actual_files.remove("_manifest.txt")
            
            # 找出在清单中，但完全没有被扫描到的文件
            completely_missing_files = expected_files - actual_files
            
            if completely_missing_files:
                print(f"  🚨 发现 {len(completely_missing_files)} 个文件完全丢失 (0个片段被扫描到):")
                for missing_file in sorted(completely_missing_files):
                    print(f"     ❌ {missing_file}")
                
                # 追加到 missing_patches.txt 中，标记为丢失所有片段
                patch_file_path = os.path.join(output_dir, "missing_patches.txt")
                with open(patch_file_path, "a", encoding="utf-8") as pf:
                    for missing_file in sorted(completely_missing_files):
                        pf.write(f"{missing_file}|ALL\n")
                print("  🤖 完全丢失的文件已追加至 missing_patches.txt (标记为ALL)")
            else:
                print("  ✅ 校验通过：没有发生整文件级别的丢失。")
        except Exception as e:
            print(f"  ⚠️ 读取或比对清单文件时发生错误: {e}")
    else:
        # 如果是单文件处理或清单本身丢失
        if len(file_groups) > 1:
            print("-" * 55)
            print("  ⚠️ 未找到 _manifest.txt。")
            print("     如果源端生成了清单，这说明清单的二维码已完全丢失，无法进行整文件丢失校验。")

    print("=" * 55)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 decode_qr_v3.py <截图目录> [输出目录] [-jN]")
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
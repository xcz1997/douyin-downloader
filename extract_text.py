"""
视频滚屏文字提取工具

从视频画面中提取动态滚动的文字内容：
- 多进程并发，动态规划并发数（默认 10GB 内存上限）
- 帧缩放至 720p 降低内存，OCR 质量无损
- 逐帧流式处理，处理完立即释放
- Rich 实时仪表盘：队列状态 / 帧级进度 / 资源占用
- RapidOCR (ONNX Runtime)，ARM Mac 原生加速

依赖安装:
    pip install opencv-python rapidocr-onnxruntime rich psutil

用法:
    python extract_text.py video.mp4
    python extract_text.py ./download_dir/ --collect
    python extract_text.py ./download_dir/ --memory 8
"""

import argparse
import gc
import os
import sys
import time
from multiprocessing import Pool, Queue
from pathlib import Path
from difflib import SequenceMatcher

MAX_WIDTH = 720
WORKER_MEMORY_GB = 2.0
SYSTEM_RESERVE_GB = 1.0

# --- 消息类型 ---
MSG_START = "start"
MSG_PROGRESS = "progress"
MSG_DONE = "done"
MSG_FAIL = "fail"

# 全局 queue，供子进程使用
_progress_queue: Queue = None


def _init_worker(q):
    global _progress_queue
    _progress_queue = q


import re

_NOISE_RE = re.compile(
    r'^[\s\d\W]{1,6}$'           # 纯符号/数字短串
    r'|^[a-zA-Z\d\s\.\-\_\×x]{1,10}$'  # 短英文/数字噪声 (rc101, m x, 15s)
    r'|^[>＞<＜\+\-\*\d\s\×x]+$'        # 纯运算符/箭头
)


def is_noise(text: str) -> bool:
    t = text.strip()
    if len(t) < 3:
        return True
    if _NOISE_RE.match(t):
        return True
    return False


def normalize(text: str) -> str:
    return re.sub(r'[\s\u3000]+', '', text.strip())


def is_similar(a: str, b: str, threshold: float) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True
    if min(len(na), len(nb)) <= 2:
        return na == nb
    len_ratio = min(len(na), len(nb)) / max(len(na), len(nb))
    if len_ratio < 0.5:
        return False
    return SequenceMatcher(None, na, nb).ratio() >= threshold


class Deduplicator:
    """全量去重器：精确匹配用 set O(1)，模糊匹配按长度分桶加速"""

    def __init__(self, similarity: float = 0.7):
        self.similarity = similarity
        self.exact_set: set[str] = set()
        self.items: list[str] = []
        self.normalized: list[str] = []

    def add(self, text: str) -> bool:
        if is_noise(text):
            return False

        norm = normalize(text)
        if norm in self.exact_set:
            return False

        if any(is_similar(norm, existing, self.similarity) for existing in self.normalized):
            return False

        self.exact_set.add(norm)
        self.items.append(text)
        self.normalized.append(norm)
        return True

    def get_lines(self) -> list[str]:
        return list(self.items)


def remove_repeated_blocks(lines: list[str], min_block: int = 5) -> list[str]:
    """检测并移除段落级重复（连续 N 行在前文中出现过）"""
    if len(lines) < min_block * 2:
        return lines

    result = list(lines[:min_block])
    i = min_block

    while i < len(lines):
        window = [normalize(l) for l in lines[i:i + min_block]]
        if len(window) < min_block:
            result.extend(lines[i:])
            break

        found_repeat = False
        norm_result = [normalize(l) for l in result]
        for j in range(len(norm_result) - min_block + 1):
            chunk = norm_result[j:j + min_block]
            matches = sum(1 for a, b in zip(window, chunk) if is_similar(a, b, 0.75))
            if matches >= min_block - 1:
                skip_end = i + min_block
                while skip_end < len(lines) and skip_end < i + min_block + 30:
                    n = normalize(lines[skip_end])
                    if any(is_similar(n, normalize(r), 0.75) for r in result):
                        skip_end += 1
                    else:
                        break
                i = skip_end
                found_repeat = True
                break

        if not found_repeat:
            result.append(lines[i])
            i += 1

    return result


def ocr_frame(ocr, frame) -> list[dict]:
    result, _ = ocr(frame)
    if not result:
        return []

    blocks = []
    for box, text, confidence in result:
        if confidence < 0.6:
            continue
        t = text.strip()
        if is_noise(t):
            continue
        y_center = (box[0][1] + box[2][1]) / 2
        x_center = (box[0][0] + box[2][0]) / 2
        blocks.append({"text": t, "y": y_center, "x": x_center})

    blocks.sort(key=lambda b: (b["y"], b["x"]))
    return blocks


def resize_frame(frame, max_width: int):
    import cv2
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    scale = max_width / w
    return cv2.resize(frame, (max_width, int(h * scale)))


def process_single_video(args_tuple) -> tuple[str, str, float]:
    """子进程入口：处理单个视频，通过 queue 发送帧级进度"""
    import cv2
    from rapidocr_onnxruntime import RapidOCR

    video_path, interval, similarity = args_tuple
    t0 = time.time()
    q = _progress_queue

    ocr = RapidOCR()

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        if q:
            q.put((MSG_FAIL, video_path, 0, 0, 0))
        return (video_path, "", 0)

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_step = max(1, int(fps * interval))
    est_frames = max(1, total_frames // frame_step)

    if q:
        q.put((MSG_START, video_path, 0, est_frames, 0))

    dedup = Deduplicator(similarity)
    frame_idx = 0
    processed = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_step == 0:
            small = resize_frame(frame, MAX_WIDTH)
            blocks = ocr_frame(ocr, small)

            for block in blocks:
                dedup.add(block["text"])

            processed += 1
            del small, blocks

            if q and processed % 3 == 0:
                q.put((MSG_PROGRESS, video_path, processed, est_frames, len(dedup.items)))

        del frame
        frame_idx += 1

    cap.release()
    del ocr
    gc.collect()

    elapsed = time.time() - t0
    lines = remove_repeated_blocks(dedup.get_lines())
    result_text = "\n".join(lines)

    if q:
        if result_text:
            q.put((MSG_DONE, video_path, len(lines), est_frames, elapsed))
        else:
            q.put((MSG_FAIL, video_path, 0, est_frames, elapsed))

    return (video_path, result_text, elapsed)


def find_videos(path: str) -> list[str]:
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm"}
    p = Path(path)
    if p.is_file() and p.suffix.lower() in video_exts:
        return [str(p)]
    if p.is_dir():
        videos = []
        for ext in video_exts:
            videos.extend(str(f) for f in p.rglob(f"*{ext}"))
        videos.sort()
        return videos
    return []


def compute_workers(memory_limit_gb: float, video_count: int) -> int:
    available = memory_limit_gb - SYSTEM_RESERVE_GB
    by_memory = max(1, int(available / WORKER_MEMORY_GB))
    cpu_count = os.cpu_count() or 4
    by_cpu = max(1, cpu_count // 2)
    return min(by_memory, by_cpu, video_count)


def get_output_path(video_path: str, args, input_root) -> Path:
    vp = Path(video_path).resolve()
    if args.output:
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / (vp.stem + "_text.txt")
    elif args.collect and input_root:
        collect_dir = input_root / "TXT"
        collect_dir.mkdir(parents=True, exist_ok=True)
        return collect_dir / (vp.parent.name + ".txt")
    else:
        return vp.with_suffix(".txt")


def run_with_dashboard(videos, task_args, workers, args, input_root):
    """Rich 实时仪表盘模式"""
    import psutil
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn, TimeElapsedColumn
    from rich.layout import Layout
    from rich.text import Text
    from rich.console import Group

    total = len(videos)
    q = Queue()
    t_start = time.time()

    # 状态跟踪
    active = {}       # video_path -> {name, processed, est_frames, texts}
    completed = 0
    failed = 0
    done_log = []      # 最近完成的记录

    process = psutil.Process(os.getpid())

    def short_name(path, maxlen=45):
        name = Path(path).parent.name if Path(path).parent.name != Path(path).name else Path(path).stem
        return (name[:maxlen - 1] + "…") if len(name) > maxlen else name

    def build_display():
        # --- 资源面板 ---
        elapsed = time.time() - t_start
        mem_total = psutil.virtual_memory()
        mem_used_gb = mem_total.used / (1024 ** 3)
        mem_total_gb = mem_total.total / (1024 ** 3)
        mem_pct = mem_total.percent
        cpu_pct = psutil.cpu_percent(interval=0)

        status_text = Text()
        status_text.append(f"⏱ {elapsed:.0f}s", style="cyan")
        status_text.append(f"  │  🧠 内存 {mem_used_gb:.1f}/{mem_total_gb:.0f}GB ({mem_pct}%)", style="yellow" if mem_pct > 70 else "green")
        status_text.append(f"  │  💻 CPU {cpu_pct:.0f}%", style="yellow" if cpu_pct > 80 else "green")
        status_text.append(f"  │  📊 {completed}✓ {failed}✗ / {total}总", style="white")

        # --- 活跃队列 ---
        active_table = Table(show_header=True, header_style="bold cyan", expand=True, padding=(0, 1))
        active_table.add_column("Worker", width=8, justify="center")
        active_table.add_column("视频", ratio=3)
        active_table.add_column("进度", width=20)
        active_table.add_column("文字", width=8, justify="right")

        for i, (vpath, info) in enumerate(active.items()):
            pct = info["processed"] / max(info["est_frames"], 1) * 100
            bar_len = 15
            filled = int(bar_len * pct / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
            active_table.add_row(
                f"P{i+1}",
                short_name(vpath),
                f"{bar} {pct:3.0f}%",
                str(info["texts"]),
            )

        # 空行补齐到 workers 数
        for i in range(workers - len(active)):
            active_table.add_row(f"P{len(active)+i+1}", "[dim]空闲[/dim]", "", "")

        # --- 完成记录 ---
        log_lines = []
        for entry in done_log[-6:]:
            log_lines.append(entry)
        log_text = "\n".join(log_lines) if log_lines else "[dim]等待中...[/dim]"

        group = Group(
            status_text,
            "",
            Panel(active_table, title=f"活跃队列 ({len(active)}/{workers})", border_style="blue"),
            Panel(log_text, title=f"已完成 ({completed + failed}/{total})", border_style="green"),
        )
        return group

    pool = Pool(processes=workers, initializer=_init_worker, initargs=(q,))
    result_async = pool.map_async(process_single_video, task_args)

    try:
        with Live(build_display(), refresh_per_second=4) as live:
            while not result_async.ready() or not q.empty():
                while not q.empty():
                    try:
                        msg = q.get_nowait()
                    except Exception:
                        break

                    msg_type, vpath = msg[0], msg[1]

                    if msg_type == MSG_START:
                        _, _, processed, est_frames, _ = msg
                        active[vpath] = {"processed": processed, "est_frames": est_frames, "texts": 0}

                    elif msg_type == MSG_PROGRESS:
                        _, _, processed, est_frames, texts = msg
                        if vpath in active:
                            active[vpath]["processed"] = processed
                            active[vpath]["est_frames"] = est_frames
                            active[vpath]["texts"] = texts

                    elif msg_type == MSG_DONE:
                        _, _, texts, est_frames, elapsed = msg
                        active.pop(vpath, None)
                        completed += 1
                        name = short_name(vpath)
                        done_log.append(f"[green]  ✓ [{completed+failed}/{total}] {name} ({texts}行, {elapsed:.1f}s)[/green]")

                    elif msg_type == MSG_FAIL:
                        _, _, _, est_frames, elapsed = msg
                        active.pop(vpath, None)
                        failed += 1
                        name = short_name(vpath)
                        done_log.append(f"[red]  ✗ [{completed+failed}/{total}] {name} (无文字)[/red]")

                live.update(build_display())
                time.sleep(0.25)

            live.update(build_display())

        pool.close()
        pool.join()

        all_results = result_async.get()
        written = 0
        for video_path, text, elapsed in all_results:
            if not text:
                continue
            out_path = get_output_path(video_path, args, input_root)
            out_path.write_text(text, encoding="utf-8")
            written += 1

        total_time = time.time() - t_start
        print(f"\n{'=' * 60}")
        print(f"完成: {completed} 成功, {failed} 无文字, {written} 文件已写入, 总耗时 {total_time:.1f}s")

    except KeyboardInterrupt:
        print("\n\n⚠️  收到中断信号，正在终止所有子进程...")
        pool.terminate()
        pool.join()
        total_time = time.time() - t_start
        print(f"已终止。已完成 {completed} 个，耗时 {total_time:.1f}s")


def run_simple(videos, task_args, workers, args, input_root):
    """无 Rich 依赖的简单模式"""
    total = len(videos)
    completed = 0
    failed = 0
    t_start = time.time()

    def on_result(result):
        nonlocal completed, failed
        video_path, text, elapsed = result
        name = Path(video_path).name[:50]
        done = completed + failed + 1

        if not text:
            print(f"  [{done}/{total}] ✗ {name} (无文字)")
            failed += 1
            return

        out_path = get_output_path(video_path, args, input_root)
        out_path.write_text(text, encoding="utf-8")
        lines = len(text.splitlines())
        print(f"  [{done}/{total}] ✓ {name} ({lines}行, {elapsed:.1f}s) → {out_path.name}")
        completed += 1

    try:
        if workers == 1:
            for a in task_args:
                on_result(process_single_video(a))
        else:
            pool = Pool(processes=workers)
            try:
                for result in pool.imap_unordered(process_single_video, task_args):
                    on_result(result)
                pool.close()
                pool.join()
            except KeyboardInterrupt:
                pool.terminate()
                pool.join()
                raise

        total_time = time.time() - t_start
        print("=" * 60)
        print(f"完成: {completed} 成功, {failed} 无文字, 总耗时 {total_time:.1f}s")

    except KeyboardInterrupt:
        total_time = time.time() - t_start
        print(f"\n⚠️  已中断。已完成 {completed} 个，耗时 {total_time:.1f}s")


def main():
    parser = argparse.ArgumentParser(
        description="从视频中提取滚屏文字内容（多进程并发，内存可控）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python extract_text.py video.mp4                    # 提取单个视频
  python extract_text.py ./downloads/ --collect        # 批量，汇总到 downloads/TXT/ 下
  python extract_text.py ./downloads/ --memory 8       # 限制最大 8GB 内存
  python extract_text.py video.mp4 --interval 0.3     # 更密集采帧（快速滚屏）
  python extract_text.py video.mp4 --no-dashboard      # 关闭仪表盘，纯文本输出
        """,
    )
    parser.add_argument("path", help="视频文件或目录路径")
    parser.add_argument("--interval", type=float, default=0.5, help="采帧间隔(秒), 默认0.5")
    parser.add_argument("--similarity", type=float, default=0.7, help="去重相似度阈值(0-1), 默认0.7")
    parser.add_argument("--memory", type=float, default=10.0, help="内存上限(GB), 默认10")
    parser.add_argument("--workers", type=int, default=0, help="强制指定并发数, 0=自动")
    parser.add_argument("--output", "-o", help="输出目录, 默认与视频同目录")
    parser.add_argument("--collect", action="store_true",
                        help="汇总模式: 输出到 <输入目录>/TXT/<子目录名>.txt")
    parser.add_argument("--no-dashboard", action="store_true", help="关闭仪表盘，纯文本输出")

    args = parser.parse_args()

    try:
        import cv2  # noqa: F401
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
    except ImportError as e:
        print(f"缺少依赖: {e}")
        print("安装: pip install opencv-python rapidocr-onnxruntime")
        sys.exit(1)

    videos = find_videos(args.path)
    if not videos:
        print(f"未找到视频文件: {args.path}")
        sys.exit(1)

    if args.workers > 0:
        workers = args.workers
    else:
        workers = compute_workers(args.memory, len(videos))

    input_root = Path(args.path).resolve() if Path(args.path).is_dir() else None

    print(f"找到 {len(videos)} 个视频")
    print(f"内存上限: {args.memory}GB | 并发: {workers} 进程 | 预估占用: ~{workers * WORKER_MEMORY_GB:.0f}GB")
    print(f"帧缩放: {MAX_WIDTH}p | 采帧间隔: {args.interval}s | 去重阈值: {args.similarity}")
    print("=" * 60)

    task_args = [(v, args.interval, args.similarity) for v in videos]

    has_dashboard = False
    if not args.no_dashboard:
        try:
            import rich, psutil  # noqa: F401
            has_dashboard = True
        except ImportError:
            pass

    if has_dashboard and workers > 1:
        run_with_dashboard(videos, task_args, workers, args, input_root)
    else:
        run_simple(videos, task_args, workers, args, input_root)


if __name__ == "__main__":
    main()

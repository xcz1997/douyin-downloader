"""
抖音下载器 v4.0 — 模块化重构版

用法:
    python downloader.py -c config.yml
    python downloader.py https://v.douyin.com/xxx
    python downloader.py --replay <trace_id>
    python downloader.py --validate-cookie
    python downloader.py --generate-config
"""

import argparse
import asyncio
import sys
import uuid
import time
from pathlib import Path

from core.config import ConfigLoader
from core.models import AppConfig
from core.tracer import Tracer
from core.logger import DualLogger
from core.cookie import CookieManager
from core.api_client import DouyinAPIClient
from core.downloader_engine import DownloadEngine
from core.pipeline import DownloadPipeline
from core.dashboard import Dashboard


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抖音下载器 v4.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("urls", nargs="*", help="直接指定下载链接")
    parser.add_argument("-c", "--config", default="config.yml", help="配置文件路径")
    parser.add_argument("--save-path", help="覆盖保存目录")
    parser.add_argument("--concurrency", type=int, help="覆盖并发数")
    parser.add_argument("--verbose", action="store_true", help="控制台显示 DEBUG 日志")
    parser.add_argument("--no-dashboard", action="store_true", help="关闭仪表盘")
    parser.add_argument("--replay", metavar="TRACE_ID", help="回放指定 trace")
    parser.add_argument("--validate-cookie", action="store_true", help="仅检测 Cookie")
    parser.add_argument("--generate-config", action="store_true", help="生成默认配置")
    return parser.parse_args()


def cmd_replay(trace_id: str):
    log_dir = Path("logs")
    output = Tracer.replay(log_dir, trace_id)
    print(output)


def cmd_generate_config(path: str):
    ConfigLoader.generate_default(path)
    print(f"已生成默认配置: {path}")
    print("请编辑后重新运行。")


async def cmd_validate_cookie(config: AppConfig):
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    dl = DualLogger(log_dir=log_dir, console_level="INFO")
    log = dl.get("cookie")
    mgr = CookieManager(config, tracer=None, logger=log)
    try:
        state = await mgr.ensure_valid_cookie()
        print(f"\n✅ Cookie 有效 (来源: {state.source})")
    except Exception as e:
        print(f"\n❌ Cookie 无效: {e}")
    dl.close()


async def cmd_download(config: AppConfig, args: argparse.Namespace):
    session_id = uuid.uuid4().hex[:8]
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    console_level = "DEBUG" if args.verbose else "INFO"
    dual_logger = DualLogger(log_dir=log_dir, console_level=console_level)
    log = dual_logger.get("main")
    tracer = Tracer(log_dir=log_dir, session_id=session_id)

    log.info("抖音下载器 v4.0 启动", session_id=session_id)

    cookie_mgr = CookieManager(config, tracer=tracer, logger=dual_logger.get("cookie"))
    api = DouyinAPIClient(
        cookie_state=None,
        tracer=tracer,
        logger=dual_logger.get("api"),
        rate_limit=2.0,
        max_retries=config.retry_times,
    )
    engine = DownloadEngine(
        save_path=config.save_path,
        tracer=tracer,
        logger=dual_logger.get("engine"),
        concurrency=config.thread,
        download_music=config.download.music,
        download_cover=config.download.cover,
        download_json=config.download.json,
    )
    dashboard = Dashboard(
        total_tasks=len(config.links),
        concurrency=config.thread,
    )

    pipeline = DownloadPipeline(
        config=config, api=api, engine=engine,
        cookie_mgr=cookie_mgr, tracer=tracer,
        logger=dual_logger.get("pipeline"), dashboard=dashboard,
    )

    if not args.no_dashboard:
        dashboard.start()

    try:
        await pipeline.run()
    except KeyboardInterrupt:
        log.warn("用户中断")
    finally:
        dashboard.stop()
        await api.close()
        await engine.close()
        tracer.close()
        dual_logger.close()

    state = dashboard.get_state()
    print(f"\n{'=' * 60}")
    print(f"完成: {state['completed']}✓ {state['failed']}✗ / {state['total']}总 | 耗时 {state['elapsed']:.1f}s")
    print(f"Session: {session_id} | 日志: logs/")


def main():
    args = parse_args()

    if args.replay:
        cmd_replay(args.replay)
        return

    if args.generate_config:
        example_path = Path(args.config).parent / "config.example.yml"
        cmd_generate_config(str(example_path))
        return

    loader = ConfigLoader(args.config)

    if not Path(args.config).exists() and not args.urls:
        example_path = Path(args.config).parent / "config.example.yml"
        if not example_path.exists():
            cmd_generate_config(str(example_path))
        print(f"未找到 {args.config}，已生成示例: {example_path}")
        print(f"请复制并编辑: cp {example_path} {args.config}")
        return

    config = loader.load()

    if args.urls:
        config.links = args.urls
    if args.save_path:
        config.save_path = Path(args.save_path)
    if args.concurrency:
        config.thread = args.concurrency

    if not config.links:
        print("错误: 未指定下载链接。使用 -c config.yml 或直接传入 URL。")
        sys.exit(1)

    if args.validate_cookie:
        asyncio.run(cmd_validate_cookie(config))
        return

    config.save_path.mkdir(parents=True, exist_ok=True)
    asyncio.run(cmd_download(config, args))


if __name__ == "__main__":
    main()

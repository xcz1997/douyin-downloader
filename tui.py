"""TUI 主界面入口：python tui.py [-c config.yml]"""

import argparse

from tui.app import DownloaderApp


def main() -> None:
    ap = argparse.ArgumentParser(description="抖音下载器 TUI 主界面")
    ap.add_argument("-c", "--config", default="config.yml")
    args = ap.parse_args()
    DownloaderApp(config_path=args.config).run()


if __name__ == "__main__":
    main()

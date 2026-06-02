"""图文笔记图片转录 CLI（薄壳）。

用法:
    python transcribe_images.py ./downloads/某作者/某笔记/
    python transcribe_images.py ./downloads/           # 递归批量
    python transcribe_images.py <dir> --force --model qwen-vl-max
"""

import argparse
import os
import sys

from core.config import ConfigLoader
from core.transcribe.client import VLMClient, VLMError
from core.transcribe.runner import ImageTranscriber, find_note_dirs  # noqa: F401


def main() -> None:
    ap = argparse.ArgumentParser(description="图文笔记图片转录（VLM）")
    ap.add_argument("path", help="笔记目录或其父目录")
    ap.add_argument("-c", "--config", default="config.yml")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的文字稿")
    ap.add_argument("--model", help="覆盖 config 的模型名")
    args = ap.parse_args()

    cfg = ConfigLoader(args.config).load().transcribe
    if args.force:
        cfg.overwrite = True
    if args.model:
        cfg.model = args.model

    api_key = os.environ.get(cfg.api_key_env, "")
    try:
        client = VLMClient(base_url=cfg.base_url, model=cfg.model,
                           api_key=api_key, timeout=cfg.timeout,
                           retry=cfg.retry)
    except VLMError as exc:
        print(f"启动失败: {exc}", file=sys.stderr)
        sys.exit(1)

    transcriber = ImageTranscriber(client, cfg)
    dirs = find_note_dirs(args.path)
    if not dirs:
        print(f"未找到图文笔记目录: {args.path}", file=sys.stderr)
        sys.exit(1)

    print(f"找到 {len(dirs)} 个笔记目录")
    for i, d in enumerate(dirs, 1):
        try:
            out = transcriber.transcribe_dir(d)
            print(f"  [{i}/{len(dirs)}] {d.name} → "
                  f"{out.name if out else '跳过'}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(dirs)}] {d.name} 失败: {exc}",
                  file=sys.stderr)


if __name__ == "__main__":
    main()

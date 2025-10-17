#!/usr/bin/env python3
"""
批量将文本文件转换为 UTF-8 编码的小工具。
"""

from __future__ import annotations

import argparse
from pathlib import Path


def convert_file(path: Path, src_encoding: str, dry_run: bool) -> None:
    """将单个文件转换为 UTF-8。"""
    try:
        raw = path.read_bytes()
    except Exception as exc:  # noqa: BLE001
        print(f"[skip] {path} -> 无法读取: {exc}")
        return

    try:
        text = raw.decode(src_encoding)
    except UnicodeDecodeError:
        print(f"[skip] {path} -> 不是 {src_encoding} 编码或含非法字节")
        return

    if dry_run:
        print(f"[dry-run] {path} 可转换为 UTF-8")
        return

    path.write_text(text, encoding="utf-8")
    print(f"[done] {path} -> UTF-8")


def iter_targets(path: Path, recursive: bool) -> list[Path]:
    """根据是否递归返回要处理的文件列表。"""
    if path.is_file():
        return [path]
    if path.is_dir():
        if recursive:
            return [p for p in path.rglob("*") if p.is_file()]
        return [p for p in path.iterdir() if p.is_file()]
    print(f"[skip] {path} 不存在")
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="批量转 UTF-8（默认源编码 gbk）")
    parser.add_argument("targets", nargs="+", help="文件或目录路径")
    parser.add_argument(
        "--encoding",
        default="gbk",
        help="源文件编码（默认 gbk，可根据实际情况调整）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅检查可转换的文件，不写入",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="目录下递归处理所有文件",
    )
    args = parser.parse_args()

    for target in args.targets:
        for file_path in iter_targets(Path(target), args.recursive):
            convert_file(file_path, args.encoding, args.dry_run)


if __name__ == "__main__":
    main()

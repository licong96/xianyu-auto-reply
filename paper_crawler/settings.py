"""
配置与项目环境加载

功能：
1. 定位项目根目录（paper_crawler 的上一级）
2. 手动解析项目根 .env 写入 os.environ（common 的配置从这里读）
3. 加载 config.yaml 爬虫配置
4. 把项目根加入 sys.path，使爬虫虚拟环境能复用 common ORM
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

# 项目根目录 = paper_crawler 包的上一级
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CRAWLER_DIR = Path(__file__).resolve().parent

# 让爬虫虚拟环境可以 import common（ORM、模型、配置）
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_dotenv(path: Path) -> None:
    """最小 .env 解析（key=value，支持引号），已存在的变量不覆盖。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


# 先加载 .env，再让 common 的配置类读取
load_dotenv(PROJECT_ROOT / ".env")


def load_config() -> dict:
    """读取 config.yaml，并把相对路径解析为绝对路径。"""
    cfg = yaml.safe_load((CRAWLER_DIR / "config.yaml").read_text(encoding="utf-8"))
    browser = cfg.setdefault("browser", {})
    for key in ("user_data_dir",):
        if browser.get(key):
            p = (PROJECT_ROOT / browser[key]).resolve()
            p.mkdir(parents=True, exist_ok=True)
            browser[key] = str(p)
    crawl = cfg.setdefault("crawl", {})
    if crawl.get("download_dir"):
        p = (PROJECT_ROOT / crawl["download_dir"]).resolve()
        p.mkdir(parents=True, exist_ok=True)
        crawl["download_dir"] = str(p)
    return cfg

#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
抖音下载器 - 统一增强版
支持视频、图文、用户主页、合集等多种内容的批量下载
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse
import argparse
import yaml

# 第三方库
try:
    import aiohttp
    import requests
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
    from rich.table import Table
    from rich.panel import Panel
    from rich.live import Live
    from rich import print as rprint
except ImportError as e:
    print(f"请安装必要的依赖: pip install aiohttp requests rich pyyaml")
    sys.exit(1)

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入项目模块
from apiproxy.douyin import douyin_headers
from apiproxy.douyin.urls import Urls
from apiproxy.douyin.result import Result
from apiproxy.common.utils import Utils
from apiproxy.douyin.auth.cookie_manager import AutoCookieManager
from apiproxy.douyin.database import DataBase

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('downloader.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Rich console
console = Console()


class ContentType:
    """内容类型枚举"""
    VIDEO = "video"
    IMAGE = "image" 
    USER = "user"
    MIX = "mix"
    MUSIC = "music"
    LIVE = "live"


class DownloadStats:
    """下载统计"""
    def __init__(self):
        self.total = 0
        self.success = 0
        self.failed = 0
        self.skipped = 0
        self.start_time = time.time()
    
    @property
    def success_rate(self):
        total = self.success + self.failed + self.skipped
        return (self.success / total * 100) if total > 0 else 0
    
    @property
    def elapsed_time(self):
        return time.time() - self.start_time
    
    def to_dict(self):
        actual_total = self.success + self.failed + self.skipped
        return {
            'total': actual_total,
            'success': self.success,
            'failed': self.failed,
            'skipped': self.skipped,
            'success_rate': f"{self.success_rate:.1f}%",
            'elapsed_time': f"{self.elapsed_time:.1f}s"
        }


class RateLimiter:
    """速率限制器"""
    def __init__(self, max_per_second: float = 2):
        self.max_per_second = max_per_second
        self.min_interval = 1.0 / max_per_second
        self.last_request = 0
    
    async def acquire(self):
        """获取许可"""
        current = time.time()
        time_since_last = current - self.last_request
        if time_since_last < self.min_interval:
            await asyncio.sleep(self.min_interval - time_since_last)
        self.last_request = time.time()


class RetryManager:
    """重试管理器"""
    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.retry_delays = [1, 2, 5]  # 重试延迟
    
    async def execute_with_retry(self, func, *args, **kwargs):
        """执行函数并自动重试"""
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delays[min(attempt, len(self.retry_delays) - 1)]
                    logger.warning(f"第 {attempt + 1} 次尝试失败: {e}, {delay}秒后重试...")
                    await asyncio.sleep(delay)
        raise last_error


class UnifiedDownloader:
    """统一下载器"""
    
    def __init__(self, config_path: str = "config.yml"):
        self.config = self._load_config(config_path)
        self.urls_helper = Urls()
        self.result_helper = Result()
        self.utils = Utils()
        
        # 组件初始化
        self.stats = DownloadStats()
        self.rate_limiter = RateLimiter(max_per_second=2)
        self.retry_manager = RetryManager(max_retries=self.config.get('retry_times', 3))
        
        # Cookie与请求头（延迟初始化，支持自动获取）
        self.cookies = self.config.get('cookies') if 'cookies' in self.config else self.config.get('cookie')
        self.auto_cookie = bool(self.config.get('auto_cookie')) or (isinstance(self.config.get('cookie'), str) and self.config.get('cookie') == 'auto') or (isinstance(self.config.get('cookies'), str) and self.config.get('cookies') == 'auto')
        self.headers = {**douyin_headers}
        # 避免服务端使用brotli导致aiohttp无法解压（未安装brotli库时会出现空响应）
        self.headers['accept-encoding'] = 'gzip, deflate'
        # 增量下载与数据库
        self.increase_cfg: Dict[str, Any] = self.config.get('increase', {}) or {}
        self.enable_database: bool = bool(self.config.get('database', True))
        self.db: Optional[DataBase] = DataBase() if self.enable_database else None
        
        # 保存路径
        self.save_path = Path(self.config.get('path', './Downloaded'))
        self.save_path.mkdir(parents=True, exist_ok=True)
        
    def _load_config(self, config_path: str) -> Dict:
        """加载配置文件"""
        if not os.path.exists(config_path):
            # 兼容配置文件命名：优先 config.yml，其次 config_simple.yml
            alt_path = 'config_simple.yml'
            if os.path.exists(alt_path):
                config_path = alt_path
            else:
                # 返回一个空配置，由命令行参数决定
                return {}
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # 简化配置兼容：links/link, output_dir/path, cookie/cookies
        if 'links' in config and 'link' not in config:
            config['link'] = config['links']
        if 'output_dir' in config and 'path' not in config:
            config['path'] = config['output_dir']
        if 'cookie' in config and 'cookies' not in config:
            config['cookies'] = config['cookie']
        if isinstance(config.get('cookies'), str) and config.get('cookies') == 'auto':
            config['auto_cookie'] = True
        
        # 允许无 link（通过命令行传入）
        # 如果两者都没有，后续会在运行时提示
        
        return config
    
    def _build_cookie_string(self) -> str:
        """构建Cookie字符串"""
        if isinstance(self.cookies, str):
            return self.cookies
        elif isinstance(self.cookies, dict):
            return '; '.join([f'{k}={v}' for k, v in self.cookies.items()])
        elif isinstance(self.cookies, list):
            # 支持来自AutoCookieManager的cookies列表
            try:
                kv = {c.get('name'): c.get('value') for c in self.cookies if c.get('name') and c.get('value')}
                return '; '.join([f'{k}={v}' for k, v in kv.items()])
            except Exception:
                return ''
        return ''

    async def _initialize_cookies_and_headers(self):
        """初始化Cookie与请求头（支持自动获取）"""
        # 若配置为字符串 'auto'，视为未提供，触发自动获取
        if isinstance(self.cookies, str) and self.cookies.strip().lower() == 'auto':
            self.cookies = None
        
        # 若已显式提供cookies，则直接使用
        cookie_str = self._build_cookie_string()
        if cookie_str:
            self.headers['Cookie'] = cookie_str
            # 同时设置到全局 douyin_headers，确保所有 API 请求都能使用
            from apiproxy.douyin import douyin_headers
            douyin_headers['Cookie'] = cookie_str
            return
        
        # 自动获取Cookie
        if self.auto_cookie:
            try:
                console.print("[cyan]🔐 正在自动获取Cookie...[/cyan]")
                async with AutoCookieManager(cookie_file='cookies.pkl', headless=False) as cm:
                    cookies_list = await cm.get_cookies()
                    if cookies_list:
                        self.cookies = cookies_list
                        cookie_str = self._build_cookie_string()
                        if cookie_str:
                            self.headers['Cookie'] = cookie_str
                            # 同时设置到全局 douyin_headers，确保所有 API 请求都能使用
                            from apiproxy.douyin import douyin_headers
                            douyin_headers['Cookie'] = cookie_str
                            console.print("[green]✅ Cookie获取成功[/green]")
                            return
                console.print("[yellow]⚠️ 自动获取Cookie失败或为空，继续尝试无Cookie模式[/yellow]")
            except Exception as e:
                logger.warning(f"自动获取Cookie失败: {e}")
                console.print("[yellow]⚠️ 自动获取Cookie失败，继续尝试无Cookie模式[/yellow]")
        
        # 未能获取Cookie则不设置，使用默认headers
    
    def detect_content_type(self, url: str) -> ContentType:
        """检测URL内容类型（应在短链接解析后调用）"""
        if '/user/' in url or '/share/user/' in url:
            return ContentType.USER
        elif '/video/' in url:
            return ContentType.VIDEO
        elif '/note/' in url:
            return ContentType.IMAGE
        elif '/collection/' in url or '/mix/' in url:
            return ContentType.MIX
        elif '/music/' in url:
            return ContentType.MUSIC
        elif 'live.douyin.com' in url:
            return ContentType.LIVE
        elif 'v.douyin.com' in url:
            return ContentType.VIDEO  # 短链接未解析时的回退
        else:
            return ContentType.VIDEO  # 默认当作视频
    
    async def resolve_short_url(self, url: str) -> str:
        """解析短链接"""
        if 'v.douyin.com' in url:
            try:
                # 使用同步请求获取重定向
                response = requests.get(url, headers=self.headers, allow_redirects=True, timeout=10)
                final_url = response.url
                logger.info(f"解析短链接: {url} -> {final_url}")
                return final_url
            except Exception as e:
                logger.warning(f"解析短链接失败: {e}")
        return url
    
    def extract_id_from_url(self, url: str, content_type: ContentType = None) -> Optional[str]:
        """从URL提取ID
        
        Args:
            url: 要解析的URL
            content_type: 内容类型（可选，用于指导提取）
        """
        # 如果已知是用户页面，直接提取用户ID
        if content_type == ContentType.USER or '/user/' in url:
            user_patterns = [
                r'/user/([\w-]+)',
                r'sec_uid=([\w-]+)'
            ]
            
            for pattern in user_patterns:
                match = re.search(pattern, url)
                if match:
                    user_id = match.group(1)
                    logger.info(f"提取到用户ID: {user_id}")
                    return user_id
        
        # 视频ID模式（优先）
        video_patterns = [
            r'/video/(\d+)',
            r'/note/(\d+)',
            r'modal_id=(\d+)',
            r'aweme_id=(\d+)',
            r'item_id=(\d+)'
        ]
        
        for pattern in video_patterns:
            match = re.search(pattern, url)
            if match:
                video_id = match.group(1)
                logger.info(f"提取到视频ID: {video_id}")
                return video_id
        
        # 其他模式
        other_patterns = [
            r'/collection/(\d+)',
            r'/music/(\d+)'
        ]
        
        for pattern in other_patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        # 尝试从URL中提取数字ID
        number_match = re.search(r'(\d{15,20})', url)
        if number_match:
            video_id = number_match.group(1)
            logger.info(f"从URL提取到数字ID: {video_id}")
            return video_id
        
        logger.error(f"无法从URL提取ID: {url}")
        return None

    def _get_aweme_id_from_info(self, info: Dict) -> Optional[str]:
        """从 aweme 信息中提取 aweme_id"""
        try:
            if 'aweme_id' in info:
                return str(info.get('aweme_id'))
            # aweme_detail 结构
            return str(info.get('aweme', {}).get('aweme_id') or info.get('aweme_id'))
        except Exception:
            return None

    def _get_sec_uid_from_info(self, info: Dict) -> Optional[str]:
        """从 aweme 信息中提取作者 sec_uid"""
        try:
            return info.get('author', {}).get('sec_uid')
        except Exception:
            return None

    def _should_skip_increment(self, context: str, info: Dict, mix_id: Optional[str] = None, music_id: Optional[str] = None, sec_uid: Optional[str] = None) -> bool:
        """根据增量配置与数据库记录判断是否跳过下载"""
        if not self.db:
            return False
        aweme_id = self._get_aweme_id_from_info(info)
        if not aweme_id:
            return False

        try:
            if context == 'post' and self.increase_cfg.get('post', False):
                sec = sec_uid or self._get_sec_uid_from_info(info) or ''
                return bool(self.db.get_user_post(sec, int(aweme_id)) if aweme_id.isdigit() else None)
            if context == 'like' and self.increase_cfg.get('like', False):
                sec = sec_uid or self._get_sec_uid_from_info(info) or ''
                return bool(self.db.get_user_like(sec, int(aweme_id)) if aweme_id.isdigit() else None)
            if context == 'mix' and self.increase_cfg.get('mix', False):
                sec = sec_uid or self._get_sec_uid_from_info(info) or ''
                mid = mix_id or ''
                return bool(self.db.get_mix(sec, mid, int(aweme_id)) if aweme_id.isdigit() else None)
            if context == 'music' and self.increase_cfg.get('music', False):
                mid = music_id or ''
                return bool(self.db.get_music(mid, int(aweme_id)) if aweme_id.isdigit() else None)
        except Exception:
            return False
        return False

    def _record_increment(self, context: str, info: Dict, mix_id: Optional[str] = None, music_id: Optional[str] = None, sec_uid: Optional[str] = None):
        """下载成功后写入数据库记录"""
        if not self.db:
            return
        aweme_id = self._get_aweme_id_from_info(info)
        if not aweme_id or not aweme_id.isdigit():
            return
        try:
            if context == 'post':
                sec = sec_uid or self._get_sec_uid_from_info(info) or ''
                self.db.insert_user_post(sec, int(aweme_id), info)
            elif context == 'like':
                sec = sec_uid or self._get_sec_uid_from_info(info) or ''
                self.db.insert_user_like(sec, int(aweme_id), info)
            elif context == 'mix':
                sec = sec_uid or self._get_sec_uid_from_info(info) or ''
                mid = mix_id or ''
                self.db.insert_mix(sec, mid, int(aweme_id), info)
            elif context == 'music':
                mid = music_id or ''
                self.db.insert_music(mid, int(aweme_id), info)
        except Exception:
            pass
    
    async def download_single_video(self, url: str, progress=None) -> bool:
        """下载单个视频/图文"""
        try:
            # 解析短链接
            url = await self.resolve_short_url(url)

            # 解析后重新检测类型，短链接可能指向用户主页
            resolved_type = self.detect_content_type(url)
            if resolved_type == ContentType.USER:
                logger.info(f"短链接实际指向用户主页，转为用户下载: {url}")
                return await self.download_user_page(url)

            # 提取ID
            video_id = self.extract_id_from_url(url, ContentType.VIDEO)
            if not video_id:
                logger.error(f"无法从URL提取ID: {url}")
                return False
            
            # 如果没有提取到视频ID，尝试作为视频ID直接使用
            if not video_id and '/user/' not in url:
                # 可能短链接直接包含了视频ID
                video_id = url.split('/')[-2] if url.endswith('/') else url.split('/')[-1]
                logger.info(f"尝试从短链接路径提取ID: {video_id}")
            
            if not video_id:
                logger.error(f"无法从URL提取视频ID: {url}")
                return False
            
            # 限速
            await self.rate_limiter.acquire()
            
            # 获取视频信息
            if progress:
                progress.update(task_id=progress.task_ids[-1], description="获取视频信息...")
            
            video_info = await self.retry_manager.execute_with_retry(
                self._fetch_video_info, video_id
            )
            
            if not video_info:
                logger.error(f"无法获取视频信息: {video_id}")
                self.stats.failed += 1
                return False
            
            # 下载视频文件
            if progress:
                progress.update(task_id=progress.task_ids[-1], description="下载视频文件...")
            
            success = await self._download_media_files(video_info, progress)
            
            if success:
                self.stats.success += 1
                logger.info(f"✅ 下载成功: {url}")
            else:
                self.stats.failed += 1
                logger.error(f"❌ 下载失败: {url}")
            
            return success
            
        except Exception as e:
            logger.error(f"下载视频异常 {url}: {e}")
            self.stats.failed += 1
            return False
        finally:
            self.stats.total += 1
    
    async def _fetch_video_info(self, video_id: str) -> Optional[Dict]:
        """获取视频信息"""
        try:
            # 使用 Douyin 类
            from apiproxy.douyin.douyin import Douyin
            
            # 创建 Douyin 实例
            dy = Douyin(database=False)
            
            # 设置我们的 cookies 到 douyin_headers
            if hasattr(self, 'cookies') and self.cookies:
                cookie_str = self._build_cookie_string()
                if cookie_str:
                    from apiproxy.douyin import douyin_headers
                    douyin_headers['Cookie'] = cookie_str
                    logger.info(f"设置 Cookie 到 Douyin 类: {cookie_str[:100]}...")
            
            try:
                # 使用现有的成功实现
                result = dy.getAwemeInfo(video_id)
                if result:
                    logger.info(f"Douyin 类成功获取视频信息: {result.get('desc', '')[:30]}")
                    return result
                else:
                    logger.error("Douyin 类返回空结果")
                    
            except Exception as e:
                logger.error(f"Douyin 类获取视频信息失败: {e}")
                
        except Exception as e:
            logger.error(f"导入或使用 Douyin 类失败: {e}")
            import traceback
            traceback.print_exc()
        
        # 如果 Douyin 类失败，尝试备用接口（iesdouyin，无需X-Bogus）
        try:
            fallback_url = f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={video_id}"
            logger.info(f"尝试备用接口获取视频信息: {fallback_url}")
            
            # 设置更通用的请求头
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://www.douyin.com/',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(fallback_url, headers=headers, timeout=15) as response:
                    logger.info(f"备用接口响应状态: {response.status}")
                    if response.status != 200:
                        logger.error(f"备用接口请求失败，状态码: {response.status}")
                        return None
                    
                    text = await response.text()
                    logger.info(f"备用接口响应内容长度: {len(text)}")
                    
                    if not text:
                        logger.error("备用接口响应为空")
                        return None
                    
                    try:
                        data = json.loads(text)
                        logger.info(f"备用接口返回数据: {data}")
                        
                        item_list = (data or {}).get('item_list') or []
                        if item_list:
                            aweme_detail = item_list[0]
                            logger.info("备用接口成功获取视频信息")
                            return aweme_detail
                        else:
                            logger.error("备用接口返回的数据中没有 item_list")
                            
                    except json.JSONDecodeError as e:
                        logger.error(f"备用接口JSON解析失败: {e}")
                        logger.error(f"原始响应内容: {text}")
                        return None
                        
        except Exception as e:
            logger.error(f"备用接口获取视频信息失败: {e}")
        
        return None
    
    def _build_detail_params(self, aweme_id: str) -> str:
        """构建详情API参数"""
        # 使用与现有 douyinapi.py 相同的参数格式
        params = [
            f'aweme_id={aweme_id}',
            'device_platform=webapp',
            'aid=6383'
        ]
        return '&'.join(params)
    
    async def _download_media_files(self, video_info: Dict, progress=None) -> bool:
        """下载媒体文件"""
        try:
            # 判断类型
            is_image = bool(video_info.get('images'))
            
            # 构建保存路径
            author_name = video_info.get('author', {}).get('nickname', 'unknown')
            desc = video_info.get('desc', '')[:50].replace('/', '_')
            # 兼容 create_time 为时间戳或格式化字符串
            raw_create_time = video_info.get('create_time')
            dt_obj = None
            if isinstance(raw_create_time, (int, float)):
                dt_obj = datetime.fromtimestamp(raw_create_time)
            elif isinstance(raw_create_time, str) and raw_create_time:
                for fmt in ('%Y-%m-%d %H.%M.%S', '%Y-%m-%d_%H-%M-%S', '%Y-%m-%d %H:%M:%S'):
                    try:
                        dt_obj = datetime.strptime(raw_create_time, fmt)
                        break
                    except Exception:
                        pass
            if dt_obj is None:
                dt_obj = datetime.fromtimestamp(time.time())
            create_time = dt_obj.strftime('%Y-%m-%d_%H-%M-%S')
            
            folder_name = f"{create_time}_{desc}" if desc else create_time
            save_dir = self.save_path / author_name / folder_name
            save_dir.mkdir(parents=True, exist_ok=True)
            
            success = True
            
            if is_image:
                # 下载图文（无水印）
                images = video_info.get('images', [])
                for i, img in enumerate(images):
                    url_list = img.get('url_list', [])
                    img_url = self._get_best_quality_url(url_list)
                    if img_url:
                        file_path = save_dir / f"image_{i+1}.jpg"
                        if await self._download_file(img_url, file_path, fallback_urls=url_list):
                            logger.info(f"下载图片 {i+1}/{len(images)}: {file_path.name}")
                        else:
                            success = False
                        await asyncio.sleep(0.3)  # 避免请求过快被限流
            else:
                # 下载视频（无水印）
                video_url = self._get_no_watermark_url(video_info)
                if video_url:
                    # 收集所有可用的视频URL作为备选
                    video_fallbacks = []
                    for key in ('play_addr', 'play_addr_h264', 'download_addr'):
                        addr = video_info.get('video', {}).get(key)
                        if addr and addr.get('url_list'):
                            video_fallbacks.extend(addr['url_list'])
                    file_path = save_dir / f"{folder_name}.mp4"
                    if await self._download_file(video_url, file_path, fallback_urls=video_fallbacks):
                        logger.info(f"下载视频: {file_path.name}")
                    else:
                        success = False

                # 下载音频
                if self.config.get('music', True):
                    music_url = self._get_music_url(video_info)
                    if music_url:
                        file_path = save_dir / f"{folder_name}_music.mp3"
                        await self._download_file(music_url, file_path)
            
            # 下载封面
            if self.config.get('cover', True):
                cover_urls = video_info.get('video', {}).get('cover', {}).get('url_list', [])
                cover_url = self._get_cover_url(video_info)
                if cover_url:
                    file_path = save_dir / f"{folder_name}_cover.jpg"
                    await self._download_file(cover_url, file_path, fallback_urls=cover_urls)
            
            # 保存JSON数据
            if self.config.get('json', True):
                json_path = save_dir / f"{folder_name}_data.json"
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(video_info, f, ensure_ascii=False, indent=2)
            
            return success
            
        except Exception as e:
            logger.error(f"下载媒体文件失败: {e}")
            return False
    
    def _get_no_watermark_url(self, video_info: Dict) -> Optional[str]:
        """获取无水印视频URL"""
        try:
            # 优先使用play_addr_h264
            play_addr = video_info.get('video', {}).get('play_addr_h264') or \
                       video_info.get('video', {}).get('play_addr')
            
            if play_addr:
                url_list = play_addr.get('url_list', [])
                if url_list:
                    # 替换URL以获取无水印版本
                    url = url_list[0]
                    url = url.replace('playwm', 'play')
                    url = url.replace('720p', '1080p')
                    return url
            
            # 备用：download_addr
            download_addr = video_info.get('video', {}).get('download_addr')
            if download_addr:
                url_list = download_addr.get('url_list', [])
                if url_list:
                    return url_list[0]
                    
        except Exception as e:
            logger.error(f"获取无水印URL失败: {e}")
        
        return None
    
    def _get_best_quality_url(self, url_list: List[str]) -> Optional[str]:
        """获取最高质量的URL"""
        if not url_list:
            return None
        
        # 优先选择包含特定关键词的URL
        for keyword in ['1080', 'origin', 'high']:
            for url in url_list:
                if keyword in url:
                    return url
        
        # 返回第一个
        return url_list[0]
    
    def _get_music_url(self, video_info: Dict) -> Optional[str]:
        """获取音乐URL"""
        try:
            music = video_info.get('music', {})
            play_url = music.get('play_url', {})
            url_list = play_url.get('url_list', [])
            return url_list[0] if url_list else None
        except:
            return None
    
    def _get_cover_url(self, video_info: Dict) -> Optional[str]:
        """获取封面URL"""
        try:
            cover = video_info.get('video', {}).get('cover', {})
            url_list = cover.get('url_list', [])
            return self._get_best_quality_url(url_list)
        except:
            return None
    
    async def _download_file(self, url: str, save_path: Path, fallback_urls: List[str] = None) -> bool:
        """下载文件，支持备选URL重试"""
        try:
            if save_path.exists():
                logger.info(f"文件已存在，跳过: {save_path.name}")
                return True

            # 构建候选URL列表：主URL + 备选URL
            urls_to_try = [url]
            if fallback_urls:
                urls_to_try.extend(u for u in fallback_urls if u != url)

            # 下载用的headers去掉referer，部分CDN会拦截
            dl_headers = {k: v for k, v in self.headers.items() if k.lower() != 'referer'}

            async with aiohttp.ClientSession() as session:
                for try_url in urls_to_try:
                    try:
                        async with session.get(try_url, headers=dl_headers) as response:
                            if response.status == 200:
                                content = await response.read()
                                with open(save_path, 'wb') as f:
                                    f.write(content)
                                return True
                            elif response.status == 403 and try_url != urls_to_try[-1]:
                                logger.warning(f"下载返回403，尝试备选URL")
                                await asyncio.sleep(0.5)
                                continue
                            else:
                                logger.error(f"下载失败，状态码: {response.status}")
                    except Exception as e:
                        logger.warning(f"下载异常: {e}")
                        if try_url != urls_to_try[-1]:
                            continue

            return False

        except Exception as e:
            logger.error(f"下载文件失败 {url}: {e}")
            return False
    
    async def download_user_page(self, url: str) -> bool:
        """下载用户主页内容"""
        try:
            # 提取用户ID
            user_id = self.extract_id_from_url(url, ContentType.USER)
            if not user_id:
                logger.error(f"无法从URL提取用户ID: {url}")
                return False
            
            console.print(f"\n[cyan]正在获取用户 {user_id} 的作品列表...[/cyan]")
            
            # 根据配置下载不同类型的内容
            mode = self.config.get('mode', ['post'])
            if isinstance(mode, str):
                mode = [mode]
            
            # 增加总任务数统计
            total_posts = 0
            if 'post' in mode:
                total_posts += self.config.get('number', {}).get('post', 0) or 1
            if 'like' in mode:
                total_posts += self.config.get('number', {}).get('like', 0) or 1
            if 'mix' in mode:
                total_posts += self.config.get('number', {}).get('allmix', 0) or 1
            
            self.stats.total += total_posts
            
            for m in mode:
                if m == 'post':
                    await self._download_user_posts(user_id)
                elif m == 'like':
                    await self._download_user_likes(user_id)
                elif m == 'mix':
                    await self._download_user_mixes(user_id)
            
            return True
            
        except Exception as e:
            logger.error(f"下载用户主页失败: {e}")
            return False
    
    async def _download_user_posts(self, user_id: str):
        """下载用户发布的作品"""
        max_count = self.config.get('number', {}).get('post', 0)
        cursor = 0
        downloaded = 0
        
        console.print(f"\n[green]开始下载用户发布的作品...[/green]")

        # 先获取全部作品并打印网页URL
        all_posts = await self._fetch_user_posts(user_id, 0)
        if all_posts:
            aweme_all = all_posts.get('aweme_list', [])
            if aweme_all:
                console.print(f"\n[cyan]📋 用户作品网页地址（共 {len(aweme_all)} 个）:[/cyan]")
                for idx, aweme in enumerate(aweme_all, 1):
                    aid = aweme.get('aweme_id', '')
                    atype = aweme.get('awemeType', 0)
                    desc = aweme.get('desc', '')[:30]
                    if atype == 1:
                        web_url = f"https://www.douyin.com/note/{aid}"
                    else:
                        web_url = f"https://www.douyin.com/video/{aid}"
                    console.print(f"  {idx:>3}. {web_url}  [dim]{desc}[/dim]")
                console.print()

        total_count = len(all_posts.get('aweme_list', [])) if all_posts else 0
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeRemainingColumn(),
            console=console,
            transient=True
        ) as progress:
            task_id = progress.add_task(
                "下载作品",
                total=max(total_count, 1)
            )

            while True:
                # 限速
                await self.rate_limiter.acquire()

                # 复用已获取的数据，避免重复请求
                if all_posts and cursor == 0:
                    posts_data = all_posts
                    all_posts = None  # 只用一次
                else:
                    posts_data = await self._fetch_user_posts(user_id, cursor)
                if not posts_data:
                    break

                aweme_list = posts_data.get('aweme_list', [])
                if not aweme_list:
                    break

                # 下载作品
                for aweme in aweme_list:
                    if max_count > 0 and downloaded >= max_count:
                        console.print(f"[yellow]已达到下载数量限制: {max_count}[/yellow]")
                        return

                    # 时间过滤
                    if not self._check_time_filter(aweme):
                        continue

                    desc = (aweme.get('desc') or '')[:20]
                    progress.update(
                        task_id,
                        description=f"下载作品 {downloaded + 1}/{total_count} {desc}"
                    )

                    # 增量判断
                    if self._should_skip_increment('post', aweme, sec_uid=user_id):
                        continue

                    # 下载
                    success = await self._download_media_files(aweme, progress)

                    if success:
                        downloaded += 1
                        self.stats.success += 1
                        progress.update(task_id, completed=downloaded)
                        self._record_increment('post', aweme, sec_uid=user_id)
                    else:
                        self.stats.failed += 1

                # 检查是否有更多
                if not posts_data.get('has_more'):
                    break

                cursor = posts_data.get('max_cursor', 0)
        
        console.print(f"[green]✅ 用户作品下载完成，共下载 {downloaded} 个[/green]")
    
    async def _fetch_user_posts(self, user_id: str, cursor: int = 0) -> Optional[Dict]:
        """获取用户作品列表"""
        try:
            # 使用 Douyin 类的 getUserInfo 方法
            from apiproxy.douyin.douyin import Douyin
            
            # 创建 Douyin 实例
            dy = Douyin(database=False)
            
            # 获取用户作品列表
            result = dy.getUserInfo(
                user_id, 
                "post", 
                35, 
                0,  # 不限制数量
                False,  # 不启用增量
                "",  # start_time
                ""   # end_time
            )
            
            if result:
                logger.info(f"Douyin 类成功获取用户作品列表，共 {len(result)} 个作品")
                # 转换为期望的格式
                return {
                    'status_code': 0,
                    'aweme_list': result,
                    'max_cursor': cursor,
                    'has_more': False
                }
            else:
                logger.error("Douyin 类返回空结果")
                return None
                
        except Exception as e:
            logger.error(f"获取用户作品列表失败: {e}")
            import traceback
            traceback.print_exc()
        
        return None
    
    async def _download_user_likes(self, user_id: str):
        """下载用户喜欢的作品"""
        max_count = 0
        try:
            max_count = int(self.config.get('number', {}).get('like', 0))
        except Exception:
            max_count = 0
        cursor = 0
        downloaded = 0

        console.print(f"\n[green]开始下载用户喜欢的作品...[/green]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
            transient=True
        ) as progress:
            task_id = progress.add_task("下载喜欢", total=None)

            while True:
                # 限速
                await self.rate_limiter.acquire()

                # 获取喜欢列表
                likes_data = await self._fetch_user_likes(user_id, cursor)
                if not likes_data:
                    break

                aweme_list = likes_data.get('aweme_list', [])
                if not aweme_list:
                    break

                # 下载作品
                for aweme in aweme_list:
                    if max_count > 0 and downloaded >= max_count:
                        console.print(f"[yellow]已达到下载数量限制: {max_count}[/yellow]")
                        return

                    if not self._check_time_filter(aweme):
                        continue

                    desc = (aweme.get('desc') or '')[:20]
                    progress.update(
                        task_id,
                        description=f"下载喜欢 {downloaded + 1} {desc}"
                    )

                    # 增量判断
                    if self._should_skip_increment('like', aweme, sec_uid=user_id):
                        continue

                    success = await self._download_media_files(aweme, progress)

                    if success:
                        downloaded += 1
                        progress.update(task_id, completed=downloaded)
                        self._record_increment('like', aweme, sec_uid=user_id)
                    else:
                        progress.update(task_id, description="[red]下载失败[/red]")

                # 翻页
                if not likes_data.get('has_more'):
                    break
                cursor = likes_data.get('max_cursor', 0)

        console.print(f"[green]✅ 喜欢作品下载完成，共下载 {downloaded} 个[/green]")

    async def _fetch_user_likes(self, user_id: str, cursor: int = 0) -> Optional[Dict]:
        """获取用户喜欢的作品列表"""
        try:
            params_list = [
                f'sec_user_id={user_id}',
                f'max_cursor={cursor}',
                'count=35',
                'aid=6383',
                'device_platform=webapp',
                'channel=channel_pc_web',
                'pc_client_type=1',
                'version_code=170400',
                'version_name=17.4.0',
                'cookie_enabled=true',
                'screen_width=1920',
                'screen_height=1080',
                'browser_language=zh-CN',
                'browser_platform=MacIntel',
                'browser_name=Chrome',
                'browser_version=122.0.0.0',
                'browser_online=true'
            ]
            params = '&'.join(params_list)

            api_url = self.urls_helper.USER_FAVORITE_A

            try:
                xbogus = self.utils.getXbogus(params)
                full_url = f"{api_url}{params}&X-Bogus={xbogus}"
            except Exception as e:
                logger.warning(f"获取X-Bogus失败: {e}, 尝试不带X-Bogus")
                full_url = f"{api_url}{params}"

            logger.info(f"请求用户喜欢列表: {full_url[:100]}...")

            async with aiohttp.ClientSession() as session:
                async with session.get(full_url, headers=self.headers, timeout=10) as response:
                    if response.status != 200:
                        logger.error(f"请求失败，状态码: {response.status}")
                        return None

                    text = await response.text()
                    if not text:
                        logger.error("响应内容为空")
                        return None

                    data = json.loads(text)
                    if data.get('status_code') == 0:
                        return data
                    else:
                        logger.error(f"API返回错误: {data.get('status_msg', '未知错误')}")
                        return None
        except Exception as e:
            logger.error(f"获取用户喜欢列表失败: {e}")
        return None

    async def _download_user_mixes(self, user_id: str):
        """下载用户的所有合集（按配置可限制数量）"""
        max_allmix = 0
        try:
            # 兼容旧键名 allmix 或 mix
            number_cfg = self.config.get('number', {}) or {}
            max_allmix = int(number_cfg.get('allmix', number_cfg.get('mix', 0)) or 0)
        except Exception:
            max_allmix = 0

        cursor = 0
        fetched = 0

        console.print(f"\n[green]开始获取用户合集列表...[/green]")
        while True:
            await self.rate_limiter.acquire()
            mix_list_data = await self._fetch_user_mix_list(user_id, cursor)
            if not mix_list_data:
                break

            mix_infos = mix_list_data.get('mix_infos') or []
            if not mix_infos:
                break

            for mix in mix_infos:
                if max_allmix > 0 and fetched >= max_allmix:
                    console.print(f"[yellow]已达到合集数量限制: {max_allmix}[/yellow]")
                    return
                mix_id = mix.get('mix_id')
                mix_name = mix.get('mix_name', '')
                console.print(f"[cyan]下载合集[/cyan]: {mix_name} ({mix_id})")
                await self._download_mix_by_id(mix_id)
                fetched += 1

            if not mix_list_data.get('has_more'):
                break
            cursor = mix_list_data.get('cursor', 0)

        console.print(f"[green]✅ 用户合集下载完成，共处理 {fetched} 个[/green]")

    async def _fetch_user_mix_list(self, user_id: str, cursor: int = 0) -> Optional[Dict]:
        """获取用户合集列表"""
        try:
            params_list = [
                f'sec_user_id={user_id}',
                f'cursor={cursor}',
                'count=35',
                'aid=6383',
                'device_platform=webapp',
                'channel=channel_pc_web',
                'pc_client_type=1',
                'version_code=170400',
                'version_name=17.4.0',
                'cookie_enabled=true',
                'screen_width=1920',
                'screen_height=1080',
                'browser_language=zh-CN',
                'browser_platform=MacIntel',
                'browser_name=Chrome',
                'browser_version=122.0.0.0',
                'browser_online=true'
            ]
            params = '&'.join(params_list)

            api_url = self.urls_helper.USER_MIX_LIST
            try:
                xbogus = self.utils.getXbogus(params)
                full_url = f"{api_url}{params}&X-Bogus={xbogus}"
            except Exception as e:
                logger.warning(f"获取X-Bogus失败: {e}, 尝试不带X-Bogus")
                full_url = f"{api_url}{params}"

            logger.info(f"请求用户合集列表: {full_url[:100]}...")
            async with aiohttp.ClientSession() as session:
                async with session.get(full_url, headers=self.headers, timeout=10) as response:
                    if response.status != 200:
                        logger.error(f"请求失败，状态码: {response.status}")
                        return None
                    text = await response.text()
                    if not text:
                        logger.error("响应内容为空")
                        return None
                    data = json.loads(text)
                    if data.get('status_code') == 0:
                        return data
                    else:
                        logger.error(f"API返回错误: {data.get('status_msg', '未知错误')}")
                        return None
        except Exception as e:
            logger.error(f"获取用户合集列表失败: {e}")
        return None

    async def download_mix(self, url: str) -> bool:
        """根据合集链接下载合集内所有作品"""
        try:
            mix_id = None
            for pattern in [r'/collection/(\d+)', r'/mix/detail/(\d+)']:
                m = re.search(pattern, url)
                if m:
                    mix_id = m.group(1)
                    break
            if not mix_id:
                logger.error(f"无法从合集链接提取ID: {url}")
                return False
            await self._download_mix_by_id(mix_id)
            return True
        except Exception as e:
            logger.error(f"下载合集失败: {e}")
            return False

    async def _download_mix_by_id(self, mix_id: str):
        """按合集ID下载全部作品"""
        cursor = 0
        downloaded = 0

        console.print(f"\n[green]开始下载合集 {mix_id} ...[/green]")

        while True:
            await self.rate_limiter.acquire()
            data = await self._fetch_mix_awemes(mix_id, cursor)
            if not data:
                break

            aweme_list = data.get('aweme_list') or []
            if not aweme_list:
                break

            for aweme in aweme_list:
                success = await self._download_media_files(aweme)
                if success:
                    downloaded += 1

            if not data.get('has_more'):
                break
            cursor = data.get('cursor', 0)

        console.print(f"[green]✅ 合集下载完成，共下载 {downloaded} 个[/green]")

    async def _fetch_mix_awemes(self, mix_id: str, cursor: int = 0) -> Optional[Dict]:
        """获取合集下作品列表"""
        try:
            params_list = [
                f'mix_id={mix_id}',
                f'cursor={cursor}',
                'count=35',
                'aid=6383',
                'device_platform=webapp',
                'channel=channel_pc_web',
                'pc_client_type=1',
                'version_code=170400',
                'version_name=17.4.0',
                'cookie_enabled=true',
                'screen_width=1920',
                'screen_height=1080',
                'browser_language=zh-CN',
                'browser_platform=MacIntel',
                'browser_name=Chrome',
                'browser_version=122.0.0.0',
                'browser_online=true'
            ]
            params = '&'.join(params_list)

            api_url = self.urls_helper.USER_MIX
            try:
                xbogus = self.utils.getXbogus(params)
                full_url = f"{api_url}{params}&X-Bogus={xbogus}"
            except Exception as e:
                logger.warning(f"获取X-Bogus失败: {e}, 尝试不带X-Bogus")
                full_url = f"{api_url}{params}"

            logger.info(f"请求合集作品列表: {full_url[:100]}...")
            async with aiohttp.ClientSession() as session:
                async with session.get(full_url, headers=self.headers, timeout=10) as response:
                    if response.status != 200:
                        logger.error(f"请求失败，状态码: {response.status}")
                        return None
                    text = await response.text()
                    if not text:
                        logger.error("响应内容为空")
                        return None
                    data = json.loads(text)
                    # USER_MIX 返回没有统一的 status_code，这里直接返回
                    return data
        except Exception as e:
            logger.error(f"获取合集作品失败: {e}")
        return None

    async def download_music(self, url: str) -> bool:
        """根据音乐页链接下载音乐下的所有作品（支持增量）"""
        try:
            # 提取 music_id
            music_id = None
            m = re.search(r'/music/(\d+)', url)
            if m:
                music_id = m.group(1)
            if not music_id:
                logger.error(f"无法从音乐链接提取ID: {url}")
                return False

            cursor = 0
            downloaded = 0
            limit_num = 0
            try:
                limit_num = int((self.config.get('number', {}) or {}).get('music', 0))
            except Exception:
                limit_num = 0

            console.print(f"\n[green]开始下载音乐 {music_id} 下的作品...[/green]")

            while True:
                await self.rate_limiter.acquire()
                data = await self._fetch_music_awemes(music_id, cursor)
                if not data:
                    break
                aweme_list = data.get('aweme_list') or []
                if not aweme_list:
                    break

                for aweme in aweme_list:
                    if limit_num > 0 and downloaded >= limit_num:
                        console.print(f"[yellow]已达到音乐下载数量限制: {limit_num}[/yellow]")
                        return True
                    if self._should_skip_increment('music', aweme, music_id=music_id):
                        continue
                    success = await self._download_media_files(aweme)
                    if success:
                        downloaded += 1
                        self._record_increment('music', aweme, music_id=music_id)

                if not data.get('has_more'):
                    break
                cursor = data.get('cursor', 0)

            console.print(f"[green]✅ 音乐作品下载完成，共下载 {downloaded} 个[/green]")
            return True
        except Exception as e:
            logger.error(f"下载音乐页失败: {e}")
            return False

    async def _fetch_music_awemes(self, music_id: str, cursor: int = 0) -> Optional[Dict]:
        """获取音乐下作品列表"""
        try:
            params_list = [
                f'music_id={music_id}',
                f'cursor={cursor}',
                'count=35',
                'aid=6383',
                'device_platform=webapp',
                'channel=channel_pc_web',
                'pc_client_type=1',
                'version_code=170400',
                'version_name=17.4.0',
                'cookie_enabled=true',
                'screen_width=1920',
                'screen_height=1080',
                'browser_language=zh-CN',
                'browser_platform=MacIntel',
                'browser_name=Chrome',
                'browser_version=122.0.0.0',
                'browser_online=true'
            ]
            params = '&'.join(params_list)

            api_url = self.urls_helper.MUSIC
            try:
                xbogus = self.utils.getXbogus(params)
                full_url = f"{api_url}{params}&X-Bogus={xbogus}"
            except Exception as e:
                logger.warning(f"获取X-Bogus失败: {e}, 尝试不带X-Bogus")
                full_url = f"{api_url}{params}"

            logger.info(f"请求音乐作品列表: {full_url[:100]}...")
            async with aiohttp.ClientSession() as session:
                async with session.get(full_url, headers=self.headers, timeout=10) as response:
                    if response.status != 200:
                        logger.error(f"请求失败，状态码: {response.status}")
                        return None
                    text = await response.text()
                    if not text:
                        logger.error("响应内容为空")
                        return None
                    data = json.loads(text)
                    return data
        except Exception as e:
            logger.error(f"获取音乐作品失败: {e}")
        return None
    
    def _check_time_filter(self, aweme: Dict) -> bool:
        """检查时间过滤"""
        start_time = self.config.get('start_time')
        end_time = self.config.get('end_time')
        
        if not start_time and not end_time:
            return True
        
        raw_create_time = aweme.get('create_time')
        if not raw_create_time:
            return True
        
        create_date = None
        if isinstance(raw_create_time, (int, float)):
            try:
                create_date = datetime.fromtimestamp(raw_create_time)
            except Exception:
                create_date = None
        elif isinstance(raw_create_time, str):
            for fmt in ('%Y-%m-%d %H.%M.%S', '%Y-%m-%d_%H-%M-%S', '%Y-%m-%d %H:%M:%S'):
                try:
                    create_date = datetime.strptime(raw_create_time, fmt)
                    break
                except Exception:
                    pass
        
        if create_date is None:
            return True
        
        if start_time:
            start_date = datetime.strptime(start_time, '%Y-%m-%d')
            if create_date < start_date:
                return False
        
        if end_time:
            end_date = datetime.strptime(end_time, '%Y-%m-%d')
            if create_date > end_date:
                return False
        
        return True
    
    async def run(self):
        """运行下载器"""
        # 显示启动信息
        console.print(Panel.fit(
            "[bold cyan]抖音下载器 v3.0 - 统一增强版[/bold cyan]\n"
            "[dim]支持视频、图文、用户主页、合集批量下载[/dim]",
            border_style="cyan"
        ))
        
        # 初始化Cookie与请求头
        await self._initialize_cookies_and_headers()
        
        # 获取URL列表
        urls = self.config.get('link', [])
        # 兼容：单条字符串
        if isinstance(urls, str):
            urls = [urls]
        if not urls:
            console.print("[red]没有找到要下载的链接！[/red]")
            return
        
        # 解析短链接并分析URL类型
        console.print(f"\n[cyan]📊 链接分析[/cyan]")
        resolved_urls = []
        for url in urls:
            resolved = await self.resolve_short_url(url)
            content_type = self.detect_content_type(resolved)
            resolved_urls.append((resolved, content_type))
            console.print(f"  • {content_type.upper()}: {url[:50]}...")

        # 开始下载
        console.print(f"\n[green]⏳ 开始下载 {len(resolved_urls)} 个链接...[/green]\n")

        for i, (url, content_type) in enumerate(resolved_urls, 1):
            console.print(f"[{i}/{len(resolved_urls)}] 处理: {url[:80]}")
            
            if content_type == ContentType.VIDEO or content_type == ContentType.IMAGE:
                await self.download_single_video(url)
            elif content_type == ContentType.USER:
                await self.download_user_page(url)
                # 若配置包含 like 或 mix，顺带处理
                modes = self.config.get('mode', ['post'])
                if 'like' in modes:
                    user_id = self.extract_id_from_url(url, ContentType.USER)
                    if user_id:
                        await self._download_user_likes(user_id)
                if 'mix' in modes:
                    user_id = self.extract_id_from_url(url, ContentType.USER)
                    if user_id:
                        await self._download_user_mixes(user_id)
            elif content_type == ContentType.MIX:
                await self.download_mix(url)
            elif content_type == ContentType.MUSIC:
                await self.download_music(url)
            else:
                console.print(f"[yellow]不支持的内容类型: {content_type}[/yellow]")
            
            # 显示进度
            console.print(f"进度: {i}/{len(resolved_urls)} | 成功: {self.stats.success} | 失败: {self.stats.failed}")
            console.print("-" * 60)
        
        # 显示统计
        self._show_stats()
    
    def _show_stats(self):
        """显示下载统计"""
        console.print("\n" + "=" * 60)
        
        # 创建统计表格
        table = Table(title="📊 下载统计", show_header=True, header_style="bold magenta")
        table.add_column("项目", style="cyan", width=12)
        table.add_column("数值", style="green")
        
        stats = self.stats.to_dict()
        table.add_row("总任务数", str(stats['total']))
        table.add_row("成功", str(stats['success']))
        table.add_row("失败", str(stats['failed']))
        table.add_row("跳过", str(stats['skipped']))
        table.add_row("成功率", stats['success_rate'])
        table.add_row("用时", stats['elapsed_time'])
        
        console.print(table)
        console.print("\n[bold green]✅ 下载任务完成！[/bold green]")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='抖音下载器 - 统一增强版',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '-c', '--config',
        default='config.yml',
        help='配置文件路径 (默认: config.yml，自动兼容 config_simple.yml)'
    )
    
    parser.add_argument(
        '-u', '--url',
        nargs='+',
        help='直接指定要下载的URL'
    )
    parser.add_argument(
        '-p', '--path',
        default=None,
        help='保存路径 (覆盖配置文件)'
    )
    parser.add_argument(
        '--auto-cookie',
        action='store_true',
        help='自动获取Cookie（需要已安装Playwright）'
    )
    parser.add_argument(
        '--cookie',
        help='手动指定Cookie字符串，例如 "msToken=xxx; ttwid=yyy"'
    )
    
    args = parser.parse_args()
    
    # 组合配置来源：优先命令行
    temp_config = {}
    if args.url:
        temp_config['link'] = args.url
    
    # 覆盖保存路径
    if args.path:
        temp_config['path'] = args.path
    
    # Cookie配置
    if args.auto_cookie:
        temp_config['auto_cookie'] = True
        temp_config['cookies'] = 'auto'
    if args.cookie:
        temp_config['cookies'] = args.cookie
        temp_config['auto_cookie'] = False
    
    # 如果存在临时配置，则生成一个临时文件供现有构造函数使用
    if temp_config:
        # 合并文件配置（如存在）
        file_config = {}
        if os.path.exists(args.config):
            try:
                with open(args.config, 'r', encoding='utf-8') as f:
                    file_config = yaml.safe_load(f) or {}
            except Exception:
                file_config = {}
        
        # 兼容简化键名
        if 'links' in file_config and 'link' not in file_config:
            file_config['link'] = file_config['links']
        if 'output_dir' in file_config and 'path' not in file_config:
            file_config['path'] = file_config['output_dir']
        if 'cookie' in file_config and 'cookies' not in file_config:
            file_config['cookies'] = file_config['cookie']
        
        merged = {**(file_config or {}), **temp_config}
        with open('temp_config.yml', 'w', encoding='utf-8') as f:
            yaml.dump(merged, f, allow_unicode=True)
        config_path = 'temp_config.yml'
    else:
        config_path = args.config
    
    # 运行下载器
    try:
        downloader = UnifiedDownloader(config_path)
        asyncio.run(downloader.run())
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️ 用户中断下载[/yellow]")
    except Exception as e:
        console.print(f"\n[red]❌ 程序异常: {e}[/red]")
        logger.exception("程序异常")
    finally:
        # 清理临时配置
        if args.url and os.path.exists('temp_config.yml'):
            os.remove('temp_config.yml')


if __name__ == '__main__':
    main()
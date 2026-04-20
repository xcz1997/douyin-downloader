class DouyinError(Exception):
    """所有自定义异常的基类"""


class RetryableError(DouyinError):
    """可自动重试的错误"""


class RateLimitError(RetryableError):
    """API 限流 (429)"""


class NetworkError(RetryableError):
    """网络超时/连接中断"""


class CookieExpiredError(DouyinError):
    """Cookie 失效，需要用户重新获取"""


class ConfigError(DouyinError):
    """配置文件错误"""


class SkippableError(DouyinError):
    """可跳过的错误，不影响其他任务"""


class ContentNotFoundError(SkippableError):
    """作品已删除或不可见"""


class DownloadFileError(SkippableError):
    """文件下载失败（所有 URL 均不可用）"""

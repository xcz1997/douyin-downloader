# 抖音下载器 - 无水印批量下载工具

一个功能强大的抖音内容批量下载工具，支持视频、图集、音乐、直播等多种内容类型的下载。

## 📋 目录

- [快速开始](#-快速开始)
- [使用指南](#-使用指南)
- [Cookie 配置工具](#-cookie-配置工具)
- [支持的链接类型](#-支持的链接类型)
- [常见问题](#-常见问题)

## ⚡ 快速开始

### 环境要求

- **Python 3.9+**
- **操作系统**：Windows、macOS、Linux

### 安装步骤

1. **克隆项目**
```bash
git clone https://github.com/xcz1997/douyin-downloader.git
cd douyin-downloader
```

2. **安装依赖**
```bash
pip install -r requirements.txt
```

3. **配置 Cookie**（首次使用需要）
```bash
# 方式1：自动获取（推荐）
python cookie_extractor.py

# 方式2：手动获取
python get_cookies_manual.py
```

## 🚀 使用指南

### 命令行使用

```bash
# 下载单个视频（需要先配置 Cookie）
python downloader.py -u "https://v.douyin.com/xxxxx/"

# 下载用户主页（推荐）
python downloader.py -u "https://www.douyin.com/user/xxxxx"

# 自动获取 Cookie 并下载
python downloader.py --auto-cookie -u "https://www.douyin.com/user/xxxxx"

# 指定保存路径
python downloader.py -u "链接" --path "./my_videos/"

# 使用配置文件
python downloader.py --config
```

### 配置文件使用

1. **创建配置文件**
```bash
cp config.example.yml config_simple.yml
```

2. **配置示例**
```yaml
# 下载链接
link:
  - https://www.douyin.com/user/xxxxx

# 保存路径
path: ./Downloaded/

# 自动 Cookie 管理
auto_cookie: true

# 下载选项
music: true
cover: true
avatar: true
json: true

# 下载模式
mode:
  - post

# 下载数量
number:
  post: 10

# 增量下载
increase:
  post: false

# 数据库
database: true
```

3. **运行程序**
```bash
python downloader.py --config
```

### 命令行参数

```bash
python downloader.py [选项] [链接...]

选项：
  -u, --url URL          下载链接
  -p, --path PATH        保存路径
  -c, --config           使用配置文件
  --auto-cookie          自动获取 Cookie
  --cookies COOKIES      手动指定 Cookie
  -h, --help            显示帮助信息
```

## 🍪 Cookie 配置工具

### 1. cookie_extractor.py - 自动获取工具

**功能**：使用 Playwright 自动打开浏览器，自动获取 Cookie

**使用方式**：
```bash
# 安装 Playwright
pip install playwright
playwright install chromium

# 运行自动获取
python cookie_extractor.py
```

**特点**：
- ✅ 自动打开浏览器
- ✅ 支持扫码登录
- ✅ 自动检测登录状态
- ✅ 自动保存到配置文件
- ✅ 支持多种登录方式

**使用步骤**：
1. 运行 `python cookie_extractor.py`
2. 选择提取方式（推荐选择1）
3. 在打开的浏览器中完成登录
4. 程序自动提取并保存 Cookie

### 2. get_cookies_manual.py - 手动获取工具

**功能**：通过浏览器开发者工具手动获取 Cookie

**使用方式**：
```bash
python get_cookies_manual.py
```

**特点**：
- ✅ 无需安装 Playwright
- ✅ 详细的操作教程
- ✅ 支持 Cookie 验证
- ✅ 自动保存到配置文件
- ✅ 支持备份和恢复

**使用步骤**：
1. 运行 `python get_cookies_manual.py`
2. 选择"获取新的Cookie"
3. 按照教程在浏览器中获取 Cookie
4. 粘贴 Cookie 内容
5. 程序自动解析并保存

### Cookie 获取教程

#### 方法一：浏览器开发者工具

1. 打开浏览器，访问 [抖音网页版](https://www.douyin.com)
2. 登录你的抖音账号
3. 按 `F12` 打开开发者工具
4. 切换到 `Network` 标签页
5. 刷新页面，找到任意请求
6. 在请求头中找到 `Cookie` 字段
7. 复制以下关键 cookie 值：
   - `msToken`
   - `ttwid`
   - `odin_tt`
   - `passport_csrf_token`
   - `sid_guard`

#### 方法二：使用自动工具

```bash
# 推荐使用自动工具
python cookie_extractor.py
```

## 📋 支持的链接类型

### 🎬 视频内容
- **单个视频分享链接**：`https://v.douyin.com/xxxxx/`
- **单个视频直链**：`https://www.douyin.com/video/xxxxx`
- **图集作品**：`https://www.douyin.com/note/xxxxx`

### 👤 用户内容
- **用户主页**：`https://www.douyin.com/user/xxxxx`
  - 支持下载用户发布的所有作品
  - 支持下载用户喜欢的作品（需要权限）

### 📚 合集内容
- **用户合集**：`https://www.douyin.com/collection/xxxxx`
- **音乐合集**：`https://www.douyin.com/music/xxxxx`

### 🔴 直播内容
- **直播间**：`https://live.douyin.com/xxxxx`

## 🔧 常见问题

### Q: Cookie 过期怎么办？
**A**:
- 使用 `python cookie_extractor.py` 重新获取
- 或使用 `python get_cookies_manual.py` 手动获取

### Q: 下载速度慢怎么办？
**A**:
- 调整 `thread` 参数增加并发数
- 检查网络连接
- 避免同时下载过多内容

### Q: 如何批量下载？
**A**:
- 使用命令行传入多个链接或使用配置文件

### Q: 支持哪些格式？
**A**:
- 视频：MP4 格式（无水印）
- 图片：JPG 格式
- 音频：MP3 格式
- 数据：JSON 格式

## ⚖️ 法律声明

- 本项目仅供**学习交流**使用
- 请遵守相关法律法规和平台服务条款
- 不得用于商业用途或侵犯他人权益
- 下载内容请尊重原作者版权

## 📄 许可证

本项目采用 [MIT License](LICENSE) 开源许可证。

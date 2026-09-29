# 红果短剧本机 Web 下载工具

在本机运行只监听 `127.0.0.1` 的 Web 界面：按剧名或 `series_id` 查找、选择实际剧目和集数、检测客户端返回的清晰度，并逐集保存 MP4。输入《满院亲戚全是上古大妖第四季》或其 `series_id` `7688558312157088793`，可以找到官网标记的 155 集及其逐集 ID；能否保存某一集、是否有 1080 档位，以该集客户端接口的实际响应为准。

## macOS 启动

需要 macOS、Python 3.11 或 3.12、FFmpeg（命令 `ffmpeg` 和 `ffprobe` 均在 `PATH` 中）。

双击 `start.command`；首次启动会创建 `.venv` 并安装 `requirements.txt`。随后浏览器会打开 `http://127.0.0.1:8765`。也可在本目录执行：

```sh
./start.command
```

Homebrew 安装 FFmpeg 的命令是 `brew install ffmpeg`。本项目不会自动安装系统软件。

## Windows EXE

在本分支的 [Actions 构建记录](https://github.com/lijiehelloworld/short-drama-downloader/actions/workflows/build-hongguo-windows.yml)中下载 `HongguoDownloader-Windows-x64`，解压后双击 `HongguoDownloader.exe`。程序在一个 WebView2 窗口中打开本机页面；关闭窗口即停止本机服务。安装包包含 Python 和仓库已有的 FFmpeg 文件，无需另外安装这两项。Windows 10/11 x64 需要 [Microsoft WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)；多数系统已有，若启动提示缺失，请从微软安装。EXE 未签名，Windows 可能显示来源提示。

在 Windows 上本机编译：

```powershell
py -3.12 -m pip install -r hongguo-web/requirements-windows.txt
```

实际打包命令见 [工作流](../.github/workflows/build-hongguo-windows.yml)。工作流在 Windows runner 上执行单元测试、打包并调用 EXE 的 `--self-test` 检查静态资源和 FFmpeg；该检查不等于已在用户的 Windows 桌面上验证窗口交互。

## 使用流程

1. 首次使用由后台自动生成并保存设备参数，页面不显示这些参数，也无需手填。macOS 配置保存在本目录 `data/config.json`；Windows 配置保存在 `%LOCALAPPDATA%\HongguoDownloader\config.json`。下载目录可在页面修改，或用 `HONGGUO_DOWNLOAD_DIR` 环境变量提供。
2. 输入准确剧名并搜索，或输入 `series_id` 直接查找。两种方式都从红果公开网页取得剧目；选择剧目后，详情页提供逐集 `video_id`，不会把 `series_id` 当成单集 ID。
3. 整部剧只选一次清晰度，默认 1080p。页面会逐集检查，画质不足或检测失败的剧集显示为灰色且不可勾选；完成后可勾选集数，或输入如 `1-10, 15, 20-25` 的范围。检查失败的集数可点「重新检查各集」重试。
4. 点击下载。批量任务在下载前仍会逐集重新解析画质；若指定的短边分辨率已不可用，该集会标记失败，不会偷偷降级。成品以 `ffprobe` 检查宽、高后才标记完成。

macOS 默认下载目录是本目录的 `downloads/`；Windows 默认下载目录是当前用户的 `Downloads\红果短剧`。文件保存在 `<下载目录>/<剧名>/第001集.mp4`；可在页面修改为其他有写入权限的绝对路径。任务状态与配置存放在同一数据目录；服务中断后可在页面继续未完成集数。暂停会在当前集完成后生效。

## 实现边界

- 搜索与选集使用官网公开 HTML 中的 `_ROUTER_DATA`。网站更改页面结构后需要更新解析器。
- 视频解析参考原项目的移动客户端请求和签名实现。自动生成的本机标识已用于验证样例第 1 集返回的画质列表；公开网页可看集数与客户端实际可取视频可能不同。
- 画质按视频宽、高的**短边**区分。例如竖屏 `1080×1920` 归为 1080 档。`ffmpeg -c copy` 只复制原流，不做放大。
- 当前版本只在本机运行，下载为单工作线程。没有验证平台对任意剧目或 1080 画质的长期可用性。

## 检查

```sh
.venv/bin/python -m unittest discover -s tests -v
```

上游源码与许可见 [`flurl/README.md`](flurl/README.md) 和 [`flurl/LICENSE`](flurl/LICENSE)。

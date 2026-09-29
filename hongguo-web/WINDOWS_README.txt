红果短剧下载 · Windows 版

系统：Windows 10/11 x64，需要 Microsoft WebView2 Runtime。

1. 解压此压缩包。
2. 双击 HongguoDownloader.exe。程序会打开本机窗口，无需安装 Python 或 FFmpeg。
3. 输入剧名或 series_id，选择集数和清晰度，然后下载。

首次启动会在 %LOCALAPPDATA%\HongguoDownloader 保存本机配置与任务状态。
默认视频目录为当前用户的“下载\红果短剧”，也可在界面中修改。
程序只在 127.0.0.1 的随机端口启动服务；关闭窗口即停止服务。
请不要同时运行多个副本。EXE 未进行代码签名，Windows 可能显示来源提示。

若启动时提示缺少 WebView2，请从微软官方网站安装 WebView2 Runtime：
https://developer.microsoft.com/microsoft-edge/webview2/

随包 FFmpeg 文件来自本仓库的 源码_开源版/插件 目录。

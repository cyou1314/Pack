Pack 本地版（Apple Silicon / macOS 14 或更新）

本包为 ad-hoc 签名，未做 Apple 开发者签名或公证。首次打开安装脚本和 App 如被系统拦截，请在 Finder 中右键选择「打开」，按系统提示操作；不要关闭系统安全设置。

首次使用：
1. 将 Pack.app 拖到 Applications。右键打开「install.command」，完成本地服务安装。脚本使用包内 Python，不要求预装 Python 或联网安装依赖。
2. 从 Applications 右键打开 Pack.app。首次启动会把本机生成的中枢凭证迁入钥匙串，按系统提示授权。默认连接本机 127.0.0.1:8794。
3. 在成员页添加成员，由成员本人在登录容器内登录，再选择将会话交给本机引擎。

安装会在 ~/.pack/local-hub 和 ~/.pack/local-engine 建立服务与数据，在用户 LaunchAgents 安装后台服务。若已有 Pack 配置、服务目录、占用的端口或交接通道，安装会停止并保留原状，不覆盖现有部署。不要同时在已有 Pack 服务的机器上直接运行默认安装。

保留此磁盘映像以便卸载：右键打开「uninstall.command」。它停止本包服务，移除经标记的 /Applications/Pack.app 与程序文件，保留数据库、成员数据、附件、凭证、配置与备份；路径会在终端显示。

卸载后数据校验：不要直接以只读方式打开原数据库。先将保留的 pack.db、muse.sqlite 与各自目录中存在的 -wal、-shm 一并复制到独立临时目录，再对副本执行 SQLite PRAGMA integrity_check；需要核对任务时，在中枢副本查询 tasks 表。不得删除原件的 WAL/SHM，保留原数据以便恢复。

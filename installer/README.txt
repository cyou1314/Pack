Pack 本地版（Apple Silicon / macOS 14 或更新）

本包为 ad-hoc 签名，未做 Apple 开发者签名或公证。首次打开安装脚本和 App 如被系统拦截，请在 Finder 中右键选择「打开」，按系统提示操作；不要关闭系统安全设置。

磁盘映像根目录包含 BUILD-MANIFEST.txt，记录 App 版本/构建号、最低 macOS 版本、内嵌 Python/FastMCP 版本、引擎版本和源码提交。构建结束后会验证 DMG 并输出 SHA-256。

首次使用：
1. 将 Pack.app 拖到 Applications。右键打开「install.command」，完成本地服务安装。脚本使用包内 Python，不要求预装 Python 或联网安装依赖。
2. 从 Applications 右键打开 Pack.app。首次启动会把本机生成的中枢凭证迁入钥匙串，按系统提示授权。默认连接本机 127.0.0.1:8794。
3. 在成员页添加成员，由成员本人在登录容器内登录，再选择将会话交给本机引擎。

安装会在 ~/.pack/local-hub 和 ~/.pack/local-engine 建立服务与数据，在用户 LaunchAgents 安装后台服务。若已有 Pack 配置、服务目录、占用的端口或交接通道，安装会停止并保留原状，不覆盖现有部署。不要同时在已有 Pack 服务的机器上直接运行默认安装。


已有安装升级：
1. 先退出 Pack.app，打开新版本 DMG 根目录的「upgrade.command」。
2. 升级器先验证安装目录、Pack LaunchAgent 身份/权限、数据库、配置和 App 标记；随后使用当前 Hub 的在线 SQLite 备份脚本生成并验证数据库/附件备份。
3. 新版 Hub、Engine 与 Pack.app 先复制到暂存位置并做工具注册/签名检查；只有预检和备份成功后才停止服务，再替换程序文件并重启健康检查。
4. 成功后保留升级备份在 ~/.pack/local-hub/backups/upgrade-*。升级不会覆盖 pack.db、attachments、tokens、config.json、Engine data 或 Keychain。
5. 如果替换或启动失败，升级器会恢复旧程序文件并重新启动旧服务；如果自动回滚也失败，终端会给出备份与暂存路径。不要手工删除这些目录，应先保留现场。

upgrade.command 仅用于本安装器创建、且 LaunchAgent/App 标记均可验证的完整 Pack 安装。若检测到符号链接、身份不匹配、配置/数据库损坏、Pack 正在运行、端口被其他进程占用或备份验证失败，会停止且不替换程序文件。开发用 local/install-local.sh 仍是全新安装脚本，不是升级器。

保留此磁盘映像以便卸载：右键打开「uninstall.command」。它停止本包服务，移除经标记的 /Applications/Pack.app 与程序文件，保留数据库、成员数据、附件、凭证、配置与备份；路径会在终端显示。

卸载后数据校验：不要直接以只读方式打开原数据库。先将保留的 pack.db、muse.sqlite 与各自目录中存在的 -wal、-shm 一并复制到独立临时目录，再对副本执行 SQLite PRAGMA integrity_check；需要核对任务时，在中枢副本查询 tasks 表。不得删除原件的 WAL/SHM，保留原数据以便恢复。

# v1.0.0 定版安装包重建结果

- 代码：独立克隆检出 tag `v1.0.0`，提交 `40909a06c3565b595b0039d65919cd230923d800`；未修改标签或代码。
- 构建：`installer/build-dmg.sh` 默认参数，Python 3.12 来自本机 Codex runtime，依赖来自现行本地栈的 venv（lock 校验通过），引擎模板取自 `/Users/cyou/pack-headless-l2`。Swift 编译通过，含现有弃用及搜索路径警告。干净克隆没有脚本所需的 `app/Pack.app`，首次打包因此停止；按仓库 `app/make-bundle.sh` 的步骤在隔离克隆中生成 App 包后重跑成功，未改源码。打包脚本的空数据检查与隔离 Python 导入检查通过。
- 产物（只留本机）：`/Users/cyou/commander-work/Pack-local-installer-v1.0.0.dmg`；211793584 字节；SHA-256 `8c245614af779abb73900a9db7e1190f2812bf733b9ef72f9c5737c9e8d9c651`。未上传、未发布 Release。

## 隔离验收

- 测试根目录 `/Users/cyou/commander-work/v1-dmg-check`；中枢 `127.0.0.1:18795`、引擎 `127.0.0.1:47822`，launchd 使用 `com.pack.installer-test-*`。安装成功，hub、watch、engine 运行，backup 已登记；中枢列出 **64** 个工具。
- 本地注册 `v1-agent`，以本地 owner 发送 `@v1-agent v1定版安装包验收`，watcher 生成待认领任务 **#1「v1定版安装包验收」**，指派 `v1-agent`。
- 从映像复制 App 后为隔离钥匙串更改验收副本 bundle ID 为 `com.cyou.pack.v1-installer-check` 并重签，使用独立 `PACK_CONFIG_PATH` 启动。用户确认未出现钥匙串或系统安全弹窗，成员列表已显示；本地接口另核对 dispatcher、owner、v1-agent。
- 关闭验收副本后执行隔离卸载，测试 App 与程序文件移除，测试代理和端口退出，数据与配置保留。按 README 将保留的 `pack.db` 与 `muse.sqlite` 连同存在的 WAL/SHM 复制到独立目录校验；两库当时均无 WAL/SHM，副本 `PRAGMA integrity_check` 均为 `ok`，中枢副本仍有任务 #1。原数据库未用于校验。
- 映像已卸载；生产 App、现行本地栈、无头引擎与桥的原 PID 在验收前后仍在，未操作其进程与数据。

## 未实证项

- 用户只确认成员列表已显示；成员具体名字由本地接口核对，未逐名取得界面确认。
- 本次以文件复制和命令执行完成隔离验收，Finder 拖拽、右键打开安装脚本及另一台真正裸机未实证；未验证跨机安装。
- 本次转卡由本地接口发送消息触发，未在验收副本聊天输入框亲发；不代表 GUI 发消息链路已由本次实证。

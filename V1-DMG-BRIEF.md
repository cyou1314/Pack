# V1-DMG-BRIEF：v1.0.0 定版安装包重建

## 目标
用 tag `v1.0.0`（40909a0）的代码重建一键安装包 dmg，使包内含地址源改造（9ba0e15）等全部 v1.0 内容；按一键安装包关账时的既定方法做隔离安装验收。dmg 只留本机，**不发布 GitHub Release、不上传任何地方**。

## 步骤
1. 在独立克隆/worktree 检出 tag v1.0.0（不要动 repo-mirror 工作区），按 installer/build-dmg.sh 构建，产物命名 `Pack-local-installer-v1.0.0.dmg`，记录大小与 SHA-256。
2. 隔离验收（与 b5d73bc 同法）：独立目录/端口/launchd 标签（com.pack.installer-test-*），装后起本地中枢列 64 工具、watcher 转卡一单、验收副本连本地中枢加载成员；卸载后按 installer/README.txt 的副本校验法（连 -wal/-shm 复制后 integrity_check）验证数据保留。
3. 验收期间生产 App / VM 中枢 / 无头引擎 / 桥一律不动；验收副本用独立 bundle 与配置。
4. 若构建或验收需要改任何代码：停下，把问题写进结果文件报告，不要自行改码。
5. 结果写入仓库根目录 `V1-DMG-RESULT.md`（构建参数、产物哈希、隔离验收逐项结果、未实证项），提交推仓（只提交该结果文件），线程发简版报告。

## 红线
- 不发布、不上传 dmg；不打新 tag、不改 tag v1.0.0。
- 不动生产：现行本地栈（~/.pack/local-hub）、生产 App、引擎与桥的进程与数据都不碰。
- 钥匙串/系统弹窗：停下报告，等群主处理。

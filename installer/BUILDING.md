# Pack DMG 构建说明

## 当前构建边界

DMG 由 `installer/build-dmg.sh` 生成。构建脚本会打包：

- 本仓库的 SwiftUI App；
- 本仓库的 Hub 与备份脚本；
- 固定依赖锁对应的 Python 3.12 运行时；
- 外部自研 Engine 的 `headless.cjs`、`gateway-page.js`、`package.json` 与 `Electron.app`。

**Engine 源码目前不在 Pack Git 仓库中，也没有配置 Git submodule。** 默认从 `~/pack-headless-l2` 读取。因此仅克隆 Pack 仓库还不能独立构建完整 DMG；这属于当前发布可复现性缺口，不应通过复制未知版本的 Engine 来绕过。

## 构建输入

| 环境变量 | 默认值 | 要求 |
|---|---|---|
| `PACK_BUILD_PYTHON_ROOT` | `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/python` | 内含 `bin/python3` 与 `lib/python3.12` |
| `PACK_BUILD_VENV` | `~/.pack/local-hub/.venv` | Python 3.12 venv；`pip freeze` 必须与 `python-requirements.lock` 完全一致 |
| `PACK_BUILD_ENGINE` | `~/pack-headless-l2` | 内含 `app/headless.cjs`、`app/gateway-page.js`、`app/package.json` 和可执行的 `Electron.app`；headless bundle 必须包含 L2 handoff 协议 |
| `PACK_BUILD_ENGINE_LOCK` | `installer/engine.lock.json` | 经确认的 Engine 产物锁；没有锁文件时拒绝构建，不会自动生成 |
| 位置参数 1 | `~/commander-work/Pack-local-installer.dmg` | 输出 DMG；目标路径必须不存在 |

构建需要 macOS、Swift 工具链、`rsync`、`ditto`、`codesign`、`hdiutil` 和 `PlistBuddy`。终端用户安装 DMG 不需要预装 Python 或联网安装 Hub 依赖。

## Engine 产物锁

从已确认的 Engine 源码 commit 构建并准备加密 Electron 后，在独立输出位置创建锁（不可覆盖现有锁）：

```bash
python3.12 installer/engine-lock.py create \
  --engine /path/to/pack-headless-l2 \
  --source-revision "$VERIFIED_SOURCE_COMMIT" \
  --lock /path/to/engine.lock.json
```

`VERIFIED_SOURCE_COMMIT` 必须是实际来源仓库的完整 Git commit ID；工具只检查其格式，不能证明来源真实性。锁记录三个 App 文件 SHA-256、Engine 版本和 Electron 树哈希（文件内容、路径、可执行位、内部相对符号链接）。不记录用户数据、凭证、时间戳、所有者或扩展属性。签名与 Fuse 仍单独验证。锁应经审核后固定到发布仓库；不要在每次构建时重新生成以绕过校验。

目前没有实际外部 Engine 产物可供核验，因此仓库不提供伪造的 `engine.lock.json`。

## 执行

```bash
PACK_BUILD_PYTHON_ROOT="/path/to/python-3.12" \
PACK_BUILD_VENV="/path/to/pack-hub-venv" \
PACK_BUILD_ENGINE="/path/to/pack-headless-l2" \
PACK_BUILD_ENGINE_LOCK="/path/to/engine.lock.json" \
bash installer/build-dmg.sh "/path/to/output/Pack-local-installer.dmg"
```

构建脚本会先检查所有输入，并验证 headless bundle 含有 `PACK_L2_SOCKET_DIR`、`handoff.sock` 和 Keychain service 契约；还会检查 Electron Framework 的 Cookie Encryption Fuse 已启用，并验证 Electron bundle 签名。需要准备加密 Electron 时，使用 `runtime/l2-handoff/prepare-electron.py` 生成独立输出，不要修改原始 Electron.app。

构建前及 Engine 复制到 staging 后都执行锁校验。DMG 根目录包含 `engine.lock.json` 和 `BUILD-INFO.txt`，记录 Pack commit、锁中声明的 Engine source revision、Engine package version、L2 静态检查结果和构建时间。锁匹配不等于源码已可复现构建或 IPC 已完成端到端测试。

## 发布前必须补齐

1. 将 Engine 源码仓库纳入组织管理，或以 Git submodule/vendor 方式固定到 Pack。
2. 固定 Engine commit，并把该 commit 纳入 CI 与发布构建。
3. 在 macOS CI 中使用该固定 Engine 运行完整 DMG 构建和安装器验收。

在这三项完成前，Hub / Swift App 的 CI 通过不等于完整 DMG 可以从干净克隆复现。

## 群聊协作模块打包

DMG 构建与安装器使用固定 Hub 程序文件白名单，包括四个 collaboration 模块；CI 检查清单一致性。构建预检拒绝缺失/符号链接程序文件，并用打包的 Python 在 staging 中验证模块导入（不初始化数据库或启动服务）。安装器在修改本机安装根目录之前检查程序包，卸载和安全重装检查使用相同白名单。继续采用原有卸载保留数据后重装流程，不提供原地覆盖升级。协作 schema 仍默认不创建；不得将包含已启用协作数据的数据库降级到不支持协作的旧 Hub。

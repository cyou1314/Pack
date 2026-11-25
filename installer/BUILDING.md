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
| 位置参数 1 | `~/commander-work/Pack-local-installer.dmg` | 输出 DMG；目标路径必须不存在 |

构建需要 macOS、Swift 工具链、`rsync`、`ditto`、`codesign`、`hdiutil` 和 `PlistBuddy`。终端用户安装 DMG 不需要预装 Python 或联网安装 Hub 依赖。

## 执行

```bash
PACK_BUILD_PYTHON_ROOT="/path/to/python-3.12" \
PACK_BUILD_VENV="/path/to/pack-hub-venv" \
PACK_BUILD_ENGINE="/path/to/pack-headless-l2" \
bash installer/build-dmg.sh "/path/to/output/Pack-local-installer.dmg"
```

构建脚本会先检查所有输入，并验证 headless bundle 含有 `PACK_L2_SOCKET_DIR`、`handoff.sock` 和 Keychain service 契约；还会检查 Electron Framework 的 Cookie Encryption Fuse 已启用，并验证 Electron bundle 签名。需要准备加密 Electron 时，使用 `runtime/l2-handoff/prepare-electron.py` 生成独立输出，不要修改原始 Electron.app。

DMG 根目录包含 `BUILD-INFO.txt`，记录 Pack commit、Engine commit（若 Engine 不是 Git 工作树则标记为 unpinned）、Engine package version、L2 handoff 检查结果和构建时间。

## 发布前必须补齐

1. 将 Engine 源码仓库纳入组织管理，或以 Git submodule/vendor 方式固定到 Pack。
2. 固定 Engine commit，并把该 commit 纳入 CI 与发布构建。
3. 在 macOS CI 中使用该固定 Engine 运行完整 DMG 构建和安装器验收。

在这三项完成前，Hub / Swift App 的 CI 通过不等于完整 DMG 可以从干净克隆复现。

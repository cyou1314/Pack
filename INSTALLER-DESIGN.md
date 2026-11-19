# 设计：单机一体化 + 一键安装包（分发线第 2、3 件）

## 目标形态
一台 Mac 装完即用：♾️ App + 本地中枢 + 引擎，全在本机；无服务器、
无隧道、无 git 地址发布。群主现行 VM 形态保持不变、互不影响。

## 本地中枢模式（第 2 件）
- hub.py 原样复用（FastMCP+SQLite），Mac 上以 launchd 用户代理常驻
  （~/Library/LaunchAgents），监听 127.0.0.1:8794。
- watcher（room_watch.py）同为 launchd 代理，PACK_WATCH_URL 指向本机。
- 初始化：安装器首启跑 hub init（建库、默认房、生成调度员令牌），
  令牌只写本机 0600 文件 + App 钥匙串，不经过任何网络。
- App 配置：安装器把 ~/.pack/config.json 的 hubUrl 预置为
  http://127.0.0.1:8794/mcp（App 现有「手动地址」通道天然兼容；
  自动发现逻辑在本地模式下不触发也不需要）。
- 备份：沿用 hub/backup-db.sh，改 launchd 定时。

## 安装包（第 3 件）
- 内容：Pack.app（预构建、ad-hoc 签名）、hub/（hub.py + venv 构建脚本
  + room_watch.py + launchd plists）、engine/（E2 后的自研引擎）、
  安装/卸载脚本、README（右键打开说明、首启三步）。
- 形态：.dmg 拖拽装 App + 一次「安装本地服务」脚本（或 .pkg 一把梭；
  v1 先 dmg+脚本，迭代快）。
- Python 依赖：目标机不保证有 Python——venv 须在打包机预构建且
  锁定版本，或安装脚本检测/引导装 Python 3；此点是打包最大变量，
  动工前先做一次目标机无 Python 的裸机演练再定方案。
- Gatekeeper：未签名/ad-hoc 首启需右键打开（说明写清）；开发者签名
  公证 $99/年暂不办（群主 2026-11-17 定）。
- 卸载：脚本停 launchd、删 App 与服务目录；用户数据（pack.db）默认
  保留并提示位置，防误删。

## 与现行形态的关系
- 仓库同一套代码双形态：VM 部署（群主自用）与本地部署（分发）共用
  hub.py/watcher/App，仅部署脚本与配置不同；任何中枢增量仍走现有
  staging 纪律、双形态都受益。

2026-11-19 本机模拟结果：随包内嵌可重定位 Python 3.12 与锁版本依赖；跨机裸机仍待实证。

# WebRTC 直连 spike 结果（2026-11-20 晚）

## 结论

**机械链路全通、连通性未通**：信令经 Pack 房间交换 SDP 成功，两侧
aiortc peer 都能跑起来，但 ICE 停在 checking、DataChannel 未建立。
在当前网络形态下 WebRTC 不是隧道的即插即用替代；要继续需先做两处
网络调整（见下），或引入 TURN 中继（等于又要一台可达的服务器，
与初衷相悖）。

## 过程（三轮）

1. 首轮卡前置：Mac 系统 Python 3.9.6 装不上 aiortc 1.16；
   pack-agent 存的中枢地址随隧道轮换过期（fetch failed）。
   裁定后解决：借单机栈 Python 3.12 本体重建 venv；地址从
   pack-addr 私有仓刷新后以 --hub 传入。
2. 第二轮 answer（房间 #188）已到，但调度员提取正文时错位截断
   2 字符、JSON 损坏、VM 侧 peer 解包崩溃（调度员的 bug，已认）。
3. 第三轮提取修正并加 JSON 校验：answer #190 落地、VM 侧成功
   setRemoteDescription，ICE 进入 checking，60 秒内未 connected，
   通道无消息，判未通。

## 候选地址诊断（未通的根因，两侧各一）

- **VM 侧 offer 只有 host 候选、没有 srflx**：198.19.0.2（云 NAT 的
  转换地址段）+ 一个 IPv6 ULA。STUN（Google）没有产出公网反射
  候选——对端没有任何可达的 VM 地址。待查：换 STUN 服务器复测 /
  确认 VM 出口 UDP 与 NAT 映射形态。
- **Mac 侧 answer 的 srflx 是代理出口而非家庭公网**：host 候选含
  192.168.2.106（局域网）与 198.18.0.1（Clash TUN 假地址段）；
  srflx 172.235.54.137 的 raddr 指向 TUN 接口——STUN 走了代理，
  反射回的是代理服务器地址，该地址不向 Mac 转发 UDP，死路。

## 若继续，下一轮的两个动作（未排期）

1. VM 侧 STUN 诊断：多 STUN 服务器交叉测，确认能否拿到 srflx；
   拿不到则 VM 不可被直连，WebRTC 方向只剩 Mac 主动 + VM 侧
   映射可达的组合或直接放弃。
2. Mac 侧给 spike 进程加 Clash DIRECT 规则（或测试时临时关 TUN），
   让 STUN 拿到家庭真实公网反射地址后再测一轮。

信令通道本身（房间消息传 SDP）验证可用，若将来立项，信令可直接
复用中枢（专用工具/房间），不需要新基础设施。

## 附记：续诊与封存（2026-11-20 22:0x–22:2x）

群主令继续，先经 Gemini 诊断、再实测裁定：

- **Gemini 诊断**：未通符合预期；VM 拿不到 srflx 的原因排序第一位
  即「云侧/宿主机禁公网 UDP 出站」；Mac 侧正解为按进程
  PROCESS-PATH 直连（只放行 STUN 域名不够，ICE 打洞包仍会被
  TUN 拐进代理）；并给出止损清单——VM 侧 UDP 探测三家 STUN
  全灭即封存 WebRTC 线、改走 VPS 固定隧道方案；且明确若 VM
  无 srflx 则纯 P2P 无戏，TURN-over-TLS 需要公网 VPS，有 VPS
  不如直接 frp/autossh + Caddy 做固定入口。
- **调度员实测（比止损线更干脆）**：VM 上 UDP 的 `sendto` 直接
  被拒——PermissionError（Errno 1），STUN 3478/19302 与往
  1.1.1.1 的 UDP/53 皆然；exec shell 与 systemd 服务两种上下文
  一致。网络形态：网卡仅 198.19.0.2/30、网关 198.19.0.1，
  DNS 返回 198.18.150.x 假地址段。即平台层硬禁 UDP 出站，
  不是超时、不是配置问题。
- **裁定：WebRTC 线封存。** Mac 侧进程直连规则不再试（对端无
  UDP，后续步骤全部无意义）。连接稳定性回到固定入口议题：
  小服务器 + frp/autossh 或 Pinggy Pro，待群主拍板花钱。
  若将来中枢迁到一台 UDP 可用的主机，本 spike 的脚本与信令
  方式可直接复用重测。

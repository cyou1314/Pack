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

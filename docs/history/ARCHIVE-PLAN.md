# 仓库根目录归档盘点（调度员，2026-11-20 上午）

repo-mirror 根目录共 39 个 .md。分类与建议（待 Gemini 方案对账后由 Codex 落地、调度员验收）：

## 不动（根目录正本/活文件）
- README.md、SPEC.md：仓库正本。
- UI-REALIGN-BRIEF.md：施工状态卡，Codex 每轮回水必读。
- MAC-WORK-BRIEF.md：待派 Mac 四件施工简报，派单时按根目录路径引用。
- REVIEW-V1.md：现行复盘正本，与 Gemini 对谈按根目录引用。
- 设计正本（此前裁定不迁、互相引用）：LOGIN-CONTAINER-DESIGN、INSTALLER-DESIGN、MODULES-DESIGN、ROOM-SESSION-DESIGN、ATTACHMENTS-DESIGN、ATTACH-COMPLETE-DESIGN、TASK-TEMPLATES-DESIGN、MEMBER-PROFILE-TEMPLATE。

## 建议归档 docs/history/（已关账的历史简报与结果，共 22 个）
ADDR-SOURCE-BRIEF、CHAT-BRIEF、CHAT-RESULT、HEADLESS-STEP1-BRIEF、HEADLESS-STEP2-BRIEF、HEADLESS-STEP3-BRIEF、HEADLESS-STEP3-RETRY-BRIEF、HEADLESS-STEP3-RETRY2-BRIEF、INSTALLER-BRIEF、L1-APP-BRIEF、L2-BRIEF、L3-BRIEF、LOCAL-BRIEF、PROBE-E2-BRIEF、PROBE-E2-RESULT、PROBE-E2B-BRIEF、PROBE-E2B-RESULT、V1-DMG-BRIEF、V1-DMG-RESULT、SHADOW-S9-RESULT、AUDIT-2026-11-16、B2-PLAN、MAC-BATCH、MAC-DAY、E2-PRECHECK（实为 25 个，以执行时清单为准）。
注：各文档间多为「按文件名提及」，移动不破文本引用；git mv 保历史；落地后全仓 grep 文件名核对提及处，必要时在状态卡加一行新路径说明。

## Mac 本地待盘点（Gemini/Codex 通道恢复后）
- 主目录 pack-headless-backup-* 一批：preflip 备份是 L2 回滚命脉（pack-headless-l2-preflip-20261119）必须留；其余按 REVIEW48/Gemini 清单逐个定性。
- app/ 下 Pack-b1-*-check.app 验收副本一批：生产 bundle 之外可清。
- commander-work/ 下 v1-dmg-check、定版 dmg：dmg 本体留，验收目录可清。

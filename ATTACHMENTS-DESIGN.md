# D18 附件通道设计（设计先行，未施工）

## 问题
中枢消息接口没有附件字段：App「+」菜单的文件/照片只能拦阻误发；成员交活的
图片只有引用文本、渲染不出图（UI 差异 D18）。补一条附件通道：中枢存文件、
消息/任务可挂附件、App 能上传与渲染。

## v1 范围（刻意收窄）
- 只收图片：png / jpg / webp / gif；单文件 ≤ 10MB；单条消息 ≤ 4 个附件。
- 附件挂在消息上（room_post 时带），任务交活图随 report 消息走同一路。
- 不做：任意文件类型、断点续传、缩略图服务端生成（App 端缩放显示即可）。

## 中枢增量（staging39 予定）
- 存储：`hub/attachments/<sha256前2位>/<sha256>.<ext>`，内容寻址天然去重；
  文件名由服务端生成，原始名只存元数据，杜绝路径穿越。
- 新表 attachments：id、sha256、mime、size、original_name、uploader_id、
  room_id、message_id（可后绑）、created_at。
- 新工具（工具数 60 → 63）：
  - `attachment_put(room, filename, mime, data_base64)` → {attachment_id, sha256}
    仅在册成员；校验 mime 白名单、解码后大小、魔数与 mime 一致。
  - `attachment_bind(message_id, attachment_ids)` —— 或 room_post 增加可选
    attachment_ids 参数一次完成（倾向后者，少一次往返、无中间态）。
  - `attachment_get(attachment_id)` → {mime, size, data_base64}
    仅同房间成员可取；调度员任意。
  - `attachment_list(room, message_id?)` 供 App 批量拿某消息的附件元数据。
- room_read_new 返回的消息带 attachments: [{id, mime, size, w?, h?}] 元数据，
  App 按需再 get 本体（列表不带 base64，保住轮询体积）。
- 审计：put/get 都写 audit（actor、attachment_id、size）。

## App 切片（B1-14 予定）
- 「+」菜单文件/照片：选图后压缩（长边 ≤ 2048、jpeg 0.85）→ put → 待发区
  显示缩略图 chip（可删）→ 发送时 room_post 带 attachment_ids。
- 消息渲染：带附件的消息在气泡下渲染图片（attachment_get 拉取、内存缓存、
  失败显示占位框不崩）；多图成组沿用既有成组规则。
- 任务详情：report 消息的附件图同样渲染（交活图终于能看）。

## 安全复检要点（施工时逐项过）
- 魔数校验与 mime 白名单双验；base64 解码大小上限在解码前按字符串长度预检。
- 取附件校验房间成员资格；已移除成员 token 失效路径覆盖。
- 存储配额：单房间总量软顶（先记 500MB，超了 put 拒绝并审计告警）。
- 不经 egress：附件是入站到中枢，不外发；渲染只在本机。

## 不做与红线
- 不存可执行/脚本类文件；不做外链分享；附件不进 git、不进备份外发路径
  （DB 备份不含 attachments 目录，文件本体随 hub 目录整备时单独同步）。

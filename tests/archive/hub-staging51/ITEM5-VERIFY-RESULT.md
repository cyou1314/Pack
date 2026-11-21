[pair] PASS | 错误配对码拒绝且不泄信息 | status=400 body={'error': 'invalid pair code'}
[pair] PASS | 畸形 JSON 拒绝 | status=400
[pair] PASS | 正确配对码兑换成功 | status=200 keys=['member', 'display_name', 'member_id', 'token', 'room']
[pair] PASS | 同一配对码二次兑换拒绝（一码一用） | status=400
[pair] PASS | 兑换后成员 kind=external 且绑定 pack-main | {'name': 'codex-ext', 'display_name': 'Codex 外部测试', 'role': 'member', 'kind': 'external', 'provider': 'codex', 'avatar': {'glyph': 'C', 'c1': '#84CC16
[watermark] PASS | external 读绑定房看不到入群前历史 | n=0
[watermark] PASS | external 读对比房被拒 | Error calling tool 'room_read_new': forbidden: external members may only read their bound room
[watermark] PASS | external 取入群前附件被拒 | Error calling tool 'attachment_get': forbidden: attachment predates external member join
[watermark] PASS | external 附件列表不含入群前附件 | ids=[]
[watermark] PASS | external 房间列表只见绑定房 | rooms=['pack-main']
[watermark] PASS | external 能看到入群后消息 | n=1
[whitelist] PASS | 白名单外 23 个工具全拒 | all denied
[whitelist] PASS | member_list 脱敏视图无敏感键 | keys=['avatar', 'display_name', 'kind', 'last_heartbeat', 'name', 'provider', 'role']
[whitelist] PASS | 白名单内 heartbeat 可用 | {'ok': True, 'member': 'codex-ext'}
[legacy] PASS | external 向对比房发帖被拒 | Error calling tool 'room_post': forbidden: external members may only post in their bound room
[legacy] PASS | external 伪造 task_card 被拒 | Error calling tool 'room_post': kind task_card is hub-internal
[legacy] PASS | external 伪造 decision 被拒 | Error calling tool 'room_post': kind decision is dispatcher-only
[legacy] PASS | task_list 只回指派本人的任务 | ids=[1]
[legacy] PASS | external 认领未指派任务被拒 | Error calling tool 'task_claim': forbidden: external members may only claim tasks assigned to themselves
[legacy] PASS | external 认领指派本人的任务成功 | {'task_id': 1, 'status': 'claimed', 'claim_token': '[REDACTED]', 'lease_expire
[legacy] PASS | 调度员把任务转给 external 被拒（红队修复1） | Error calling tool 'task_transfer': transfer target cannot be an external member
[legacy] PASS | external 编辑他人消息被拒 | Error calling tool 'message_edit': only the author can edit this message
[quota] PASS | external 小附件上传可用 | {'attachment_id': 2, 'sha256': '6b7fa434f92a8b80aab02d9bf1a12e49ffcae424e4013a1c4f68b67e3d2bbcd0', '
[quota] PASS | external 5MB+ 附件未被接受（工具拒或传输层413） | HTTP 0: URLError: <urlopen error [Errno 32] Broken pipe>
[quota] PASS | 发帖频控 5/分生效 | sent=5 limited=True
[quota] FAIL | egress_check 60/分频控生效 | i=64
[gate] PASS | 总开关关闭后 external 鉴权被拒 | Error calling tool 'member_list': unauthorized: missing or bad token
[gate] PASS | 总开关关闭后新配对不可兑换 | 
[gate] PASS | 总开关重开后 external 恢复 | 
== TOTAL 29 PASS 28 FAIL 1
FAILED: [quota] egress_check 60/分频控生效

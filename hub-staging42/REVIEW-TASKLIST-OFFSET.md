# staging42 慢工复检：task_list 分页（P2 中枢侧）

- 只加可选参数 offset（默认 0、负数钳 0），排序口径不动
  （urgent DESC, id ASC，定稿 §15.3 与 docstring 一致）。
- 既有调用方（App、运行时、watcher 不用 task_list 分页）不传 offset，
  行为逐字节不变；LIMIT 100 仍在，翻页由调用方驱动。
- 定向 test_tasklist_offset.py 4/4：默认页升序全量、offset=3 取尾、
  越界空、负 offset 钳 0。
- App 侧配合（任务板翻页拉全量）并入 B1-16 派单 rider。

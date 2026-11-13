# App 图标来源（群主给定，Muse 已裁好）

- 下载地址：https://muse.ai/files/1388973747624569/1125007640388701/cdvk1n0fnrtj6891q54dm33i/AppIcon-1024.png
  （Muse 内置文件存储链接，2026-11-15 到期；过期后找 Muse 重新生成）
- 保存为：`app/Assets/AppIcon-1024.png`（1024×1024，圆角透明底母版）
- 下载后请把这张 PNG 本身 git add / commit / push 入库（信息：chore(app): 群主给定 App 图标），让仓库自带图标源文件；本说明文件入库后可删。
- 构建时 make-bundle.sh 会自动用它生成 AppIcon.icns 装入 Pack.app（Info.plist 已声明 CFBundleIconFile=AppIcon）。

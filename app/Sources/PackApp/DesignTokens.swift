import SwiftUI

/// Pack 设计 token（定稿 app/UI-DESIGN.md §2/§4 的数值正本）。
/// 所有视图只从这里取值，不许在视图里散写颜色/圆角/字号。
enum DesignTokens {
    // 颜色
    static let blue = Color(hex: 0x1B7CF5)          // 电光蓝，只点缀
    static let ink = Color(hex: 0x1A1C20)           // 主文字 / 群主气泡底
    static let bubbleAI = Color(hex: 0xF4F5F7)      // AI 气泡底
    static let hairline = Color(hex: 0xE4E6EA)      // 发丝线
    static let subText = Color(hex: 0x6E747E)       // 次级文字（克制使用）
    static let faintText = Color(hex: 0xA8ADB6)     // 最弱文字
    static let danger = Color(hex: 0xD03C40)        // 阻塞/耗尽/删除
    static let amber = Color(hex: 0xB87A12)         // 待批准/草稿
    static let okGreen = Color(hex: 0x1E8C50)       // 已交活/通过

    // 额度环三档（已用占比 → 环色）
    static let quotaGreen = Color(hex: 0x23B45C)
    static let quotaYellow = Color(hex: 0xF2B220)
    static let quotaRed = Color(hex: 0xE84046)

    // 圆角
    static let radiusCard: CGFloat = 8
    static let radiusBubble: CGFloat = 12
    static let radiusSmall: CGFloat = 6

    // 字号
    static let fontUI = Font.system(size: 13)
    static let fontSmall = Font.system(size: 11)
    static let fontTitle = Font.system(size: 13, weight: .semibold)

    // 动效（定稿 §8：只动变化处，150–250ms）
    static let animFast = Animation.easeInOut(duration: 0.18)
    static let animStd = Animation.easeInOut(duration: 0.22)

    // 布局
    static let railWidth: CGFloat = 56              // 收起态图标条
    static let sidebarWidth: CGFloat = 264          // 展开态侧栏
    static let boardCardRadius: CGFloat = 8
}

extension Color {
    init(hex: UInt32) {
        let r = Double((hex >> 16) & 0xFF) / 255.0
        let g = Double((hex >> 8) & 0xFF) / 255.0
        let b = Double(hex & 0xFF) / 255.0
        self.init(red: r, green: g, blue: b)
    }
}

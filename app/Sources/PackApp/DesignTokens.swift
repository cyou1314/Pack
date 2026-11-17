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
    static let shellInset: CGFloat = 12
    static let shellGap: CGFloat = 8
    static let shellRow: CGFloat = 36
    static let shellLogo: CGFloat = 30
    static let shellIcon: CGFloat = 18
    static let shellQuota: CGFloat = 38
    static let shellAvatar: CGFloat = 26
    static let shellTop: CGFloat = 20
    static let shellDivider: CGFloat = 1
    static let shellMinWidth: CGFloat = 900
    static let shellMinHeight: CGFloat = 620
    static let shellTitle = Font.system(size: 17, weight: .bold)
    static let shellHeaderHeight: CGFloat = 45
    static let shellHeaderInset: CGFloat = 25
    static let shellHeaderTitle = Font.system(size: 14, weight: .semibold)
    static let connectionDotSize: CGFloat = 6
    static let connectionBannerBackground = Color(hex: 0xFFF6E7)
    static let connectionBannerVertical: CGFloat = 8
    static let connectionDisabledOpacity: Double = 0.55
    static let summaryItemSpacing: CGFloat = 3
    static let summaryIconFont = Font.system(size: 10, weight: .semibold)
    static let summarySectionFont = Font.system(size: 11, weight: .semibold)
    static let shellSurface = Color(nsColor: .textBackgroundColor)
    static let shellSelectionOpacity: Double = 0.1
    static let quotaGradientOpacity: Double = 0.6
    static let chatContentWidth: CGFloat = 740
    static let chatBubbleWidth: CGFloat = 520
    static let chatInset: CGFloat = 24
    static let chatVerticalInset: CGFloat = 22
    static let chatSpacing: CGFloat = 14
    static let chatBubbleHorizontal: CGFloat = 14
    static let chatBubbleVertical: CGFloat = 10
    static let chatRowSpacing: CGFloat = 9
    static let chatMetadataSpacing: CGFloat = 5
    static let chatAvatar: CGFloat = 34
    static let composerControl: CGFloat = 29
    static let composerMenuWidth: CGFloat = 235
    static let composerMenuRow: CGFloat = 34
    static let composerMenuIcon: CGFloat = 16
    static let composerPillHorizontal: CGFloat = 9
    static let composerPillVertical: CGFloat = 5
    static let composerSend: CGFloat = 32
    static let composerMenuShadow: CGFloat = 10
    static let chatReceiptFont = Font.system(size: 11)
    static let boardInset: CGFloat = 16
    static let boardGap: CGFloat = 12
    static let boardCardInset: CGFloat = 10
    static let boardCardGap: CGFloat = 8
    static let boardLine: CGFloat = 1
    static let boardProgressHeight: CGFloat = 2
    static let boardAvatar: CGFloat = 20
    static let boardTabHeight: CGFloat = 32
    static let boardHighlightOpacity: Double = 0.12
    static let boardHighlightLine: CGFloat = 2
    static let approvalBackground = Color(hex: 0xFFF6E7)
    static let approvalBorder = Color(hex: 0xE6C17C)
    static let approvalButtonHorizontal: CGFloat = 10
    static let approvalButtonVertical: CGFloat = 6
    static let detailSpacing: CGFloat = 8
    static let detailPadding: CGFloat = 12
    static let detailAnimation = Animation.easeInOut(duration: 0.2)
    static let messageActionSpacing: CGFloat = 8
    static let messageActionIcon: CGFloat = 16
    static let roomDialogWidth: CGFloat = 400
    static let memberAvatarLarge: CGFloat = 64
    static let memberAvatarMedium: CGFloat = 42
    static let memberPanelWidth: CGFloat = 360
    static let memberLoginWidth: CGFloat = 500
    static let memberLoginHeight: CGFloat = 360
    static let memberGap: CGFloat = 12
    static let memberInset: CGFloat = 16
    static let memberIconRatio: CGFloat = 0.52
    static let settingsWidth: CGFloat = 680
    static let settingsInset: CGFloat = 20
    static let settingsGap: CGFloat = 12
    static let settingsFieldWidth: CGFloat = 400
    static let offlineAlertSeconds: TimeInterval = 600
    static let longMessageLines = 6
    static let longMessageThreshold = 260
    static let menuButtonSize: CGFloat = 30
    static let artifactPreviewSize: CGFloat = 180
    static let memberAccessoryRatio: CGFloat = 0.22
    static let avatarBackgrounds: [String: Color] = ["cream": Color(hex: 0xF7EEDC), "mint": Color(hex: 0xDBEFE4), "sky": Color(hex: 0xDDECF8), "peach": Color(hex: 0xF8E2D9), "lavender": Color(hex: 0xEAE1F4), "sand": Color(hex: 0xEDE4D3)]
    static let avatarPalettes: [String: Color] = ["orange": Color(hex: 0xE87924), "violet": Color(hex: 0x875CCB), "ocean": Color(hex: 0x277AB5), "forest": Color(hex: 0x35865C), "rose": Color(hex: 0xC55D85), "amber": Color(hex: 0xBC861E), "teal": Color(hex: 0x238A87), "indigo": Color(hex: 0x5364B6), "crimson": Color(hex: 0xBD4D55), "slate": Color(hex: 0x687789)]
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

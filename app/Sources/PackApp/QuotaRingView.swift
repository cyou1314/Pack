import SwiftUI

/// 额度环（定稿 §4，数值口径以本文件与 DesignTokens 为准）：
/// 弧长 = 周额度**剩余**占比，环心数字 = 剩余 %；环色按**已用**分档
/// （<70% 绿 / 70–90% 黄 / >90% 红）；扇形拼弧、平头收尾、无底圈。
/// 数据过期（>15 分钟，MemberInfo.quotaIsStale）整环转灰——过期数字
/// 不作派活依据，呈现上也不许装新鲜。无数据时只画灰色短横。
/// 预写组件：未经 Mac 编译，B1-1 基线构建时先清。
struct QuotaRingView: View {
    let usedPct: Double?
    let stale: Bool
    var size: CGFloat = 44
    var lineWidth: CGFloat = 5

    private var remaining: Double? {
        guard let used = usedPct else { return nil }
        return max(0, 100 - used)
    }

    private var tierColor: Color {
        guard let used = usedPct, !stale else { return DesignTokens.faintText }
        if used < 70 { return DesignTokens.quotaGreen }
        if used < 90 { return DesignTokens.quotaYellow }
        return DesignTokens.quotaRed
    }

    var body: some View {
        ZStack {
            if let rem = remaining {
                AnnularSector(fraction: rem / 100)
                    .fill(LinearGradient(colors: [tierColor.opacity(DesignTokens.quotaGradientOpacity), tierColor], startPoint: .topLeading, endPoint: .bottomTrailing))
                Text("\(Int(rem.rounded()))%")
                    .contentTransition(.numericText())
                    .font(.system(size: size * 0.30, weight: .semibold))
                    .foregroundColor(stale ? DesignTokens.faintText : DesignTokens.ink)
            } else {
                Text("–")
                    .font(.system(size: size * 0.30, weight: .semibold))
                    .foregroundColor(DesignTokens.faintText)
            }
        }
        .frame(width: size, height: size)
        .animation(DesignTokens.animStd, value: usedPct)
    }
}

/// 环形扇区：外弧顺时针、内弧逆时针拼成的封闭扇环，自顶部起画，
/// fraction 为占比（1 = 整圈）。端点天然平头。
struct AnnularSector: Shape {
    var fraction: Double
    var animatableData: Double {
        get { fraction }
        set { fraction = newValue }
    }

    func path(in rect: CGRect) -> Path {
        var p = Path()
        let f = min(max(fraction, 0), 1)
        guard f > 0 else { return p }
        let center = CGPoint(x: rect.midX, y: rect.midY)
        let outer = min(rect.width, rect.height) / 2
        let inner = outer * 0.72
        let start = -90.0
        let sweep = 360.0 * f
        if f >= 1 {
            p.addArc(center: center, radius: outer,
                     startAngle: .degrees(0), endAngle: .degrees(360), clockwise: false)
            p.addArc(center: center, radius: inner,
                     startAngle: .degrees(360), endAngle: .degrees(0), clockwise: true)
            p.closeSubpath()
            return p
        }
        p.addArc(center: center, radius: outer,
                 startAngle: .degrees(start), endAngle: .degrees(start + sweep), clockwise: false)
        p.addArc(center: center, radius: inner,
                 startAngle: .degrees(start + sweep), endAngle: .degrees(start), clockwise: true)
        p.closeSubpath()
        return p
    }
}

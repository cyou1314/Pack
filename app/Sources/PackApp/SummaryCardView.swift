import SwiftUI

/// 房间摘要卡（定稿 §14.1）：钉在房间顶部，三组——已定 / 进行中 / 待拍板。
/// 无状态组件：展开与否由父视图的视图模型持有（全 App 禁 @State）。
/// 空组整组隐藏；三组全空时不渲染（父视图自行决定是否占位）。
/// 预写组件：未经 Mac 编译，B1-1 基线构建时先清。
struct SummaryCardView: View {
    let summary: RoomSummary
    let expanded: Bool
    var onToggle: () -> Void = {}

    private var isEmpty: Bool {
        summary.decided.isEmpty && summary.inProgress.isEmpty && summary.pending.isEmpty
    }

    var body: some View {
        if !isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Button(action: onToggle) {
                    HStack(spacing: 6) {
                        Image(systemName: expanded ? "chevron.down" : "chevron.right")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundColor(DesignTokens.subText)
                        Text("房间摘要")
                            .font(DesignTokens.fontTitle)
                            .foregroundColor(DesignTokens.ink)
                        Spacer()
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)

                if expanded {
                    section(title: "已定", items: summary.decided)
                    section(title: "进行中", items: summary.inProgress)
                    section(title: "待拍板", items: summary.pending)
                } else {
                    Text(countsLine)
                        .font(DesignTokens.fontSmall)
                        .foregroundColor(DesignTokens.subText)
                }
            }
            .padding(12)
            .background(Color.white)
            .overlay(
                RoundedRectangle(cornerRadius: DesignTokens.radiusCard)
                    .stroke(DesignTokens.hairline, lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
        }
    }

    private var countsLine: String {
        var parts: [String] = []
        if !summary.decided.isEmpty { parts.append("已定 \(summary.decided.count)") }
        if !summary.inProgress.isEmpty { parts.append("进行中 \(summary.inProgress.count)") }
        if !summary.pending.isEmpty { parts.append("待拍板 \(summary.pending.count)") }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private func section(title: String, items: [String]) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(DesignTokens.ink)
                ForEach(items, id: \.self) { item in
                    HStack(alignment: .top, spacing: 6) {
                        Text("·").foregroundColor(DesignTokens.subText)
                        Text(item)
                            .font(DesignTokens.fontUI)
                            .foregroundColor(DesignTokens.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }
}

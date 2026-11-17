import SwiftUI

/// 房间摘要卡（定稿 §14.1）：钉在房间顶部，三组——已定 / 进行中 / 待拍板。
/// 无状态组件：展开与否由父视图的视图模型持有（全 App 禁 @State）。
/// 空组隐藏；空摘要保留标题，避免与尚未读取混淆。
/// 预写组件：未经 Mac 编译，B1-1 基线构建时先清。
struct SummaryCardView: View {
    let summary: RoomSummary
    let expanded: Bool
    var onToggle: () -> Void = {}

    private var isEmpty: Bool {
        summary.decided.isEmpty && summary.inProgress.isEmpty && summary.pending.isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.detailSpacing) {
                Button(action: onToggle) {
                    HStack(spacing: DesignTokens.shellGap) {
                        Image(systemName: expanded ? "chevron.down" : "chevron.right")
                            .font(DesignTokens.summaryIconFont)
                            .foregroundColor(DesignTokens.subText)
                        Text("房间摘要")
                            .font(DesignTokens.fontTitle)
                            .foregroundColor(DesignTokens.ink)
                        if !expanded {
                            Text("待拍板 \(summary.pending.count)")
                                .font(DesignTokens.fontSmall)
                                .foregroundColor(DesignTokens.subText)
                        }
                        Spacer()
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)

                if expanded {
                    if isEmpty { Text("暂无摘要").font(DesignTokens.fontSmall).foregroundColor(DesignTokens.subText) }
                    section(title: "已定", items: summary.decided)
                    section(title: "进行中", items: summary.inProgress)
                    section(title: "待拍板", items: summary.pending)
                }
            }
            .padding(DesignTokens.detailPadding)
            .background(Color.white)
            .overlay(
                RoundedRectangle(cornerRadius: DesignTokens.radiusCard)
                    .stroke(DesignTokens.hairline, lineWidth: DesignTokens.boardLine)
            )
            .clipShape(RoundedRectangle(cornerRadius: DesignTokens.radiusCard))
    }

    @ViewBuilder
    private func section(title: String, items: [String]) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: DesignTokens.summaryItemSpacing) {
                Text(title)
                    .font(DesignTokens.summarySectionFont)
                    .foregroundColor(DesignTokens.ink)
                ForEach(items, id: \.self) { item in
                    HStack(alignment: .top, spacing: DesignTokens.shellGap) {
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

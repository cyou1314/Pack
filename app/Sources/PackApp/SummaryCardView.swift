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
                        if expanded {
                            if let url = Bundle.main.url(forResource: "dispatcher", withExtension: "webp", subdirectory: "avatars"),
                               let avatar = NSImage(contentsOf: url) {
                                Image(nsImage: avatar).resizable().scaledToFill()
                                    .frame(width: DesignTokens.summaryAvatarSize, height: DesignTokens.summaryAvatarSize)
                                    .clipShape(RoundedRectangle(cornerRadius: DesignTokens.radiusSmall))
                            }
                        } else {
                            Image(systemName: "chevron.right")
                                .font(DesignTokens.summaryIconFont)
                                .foregroundColor(DesignTokens.subText)
                        }
                        Text("房间摘要")
                            .font(DesignTokens.fontTitle)
                            .foregroundColor(DesignTokens.ink)
                        if expanded {
                            tag("钉在顶部", color: DesignTokens.blue)
                        } else {
                            Text("待拍板 \(summary.pending.count)")
                                .font(DesignTokens.fontSmall)
                                .foregroundColor(DesignTokens.subText)
                        }
                        Spacer()
                        if expanded {
                            Text(summary.updatedAt.map { "调度员维护 · \(Date(timeIntervalSince1970: $0).formatted(date: .abbreviated, time: .shortened)) 更新" } ?? "调度员维护 · 更新时间暂无")
                                .font(DesignTokens.fontSmall)
                                .foregroundColor(DesignTokens.subText)
                        }
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)

                if expanded {
                    if isEmpty { Text("暂无摘要").font(DesignTokens.fontSmall).foregroundColor(DesignTokens.subText) }
                    VStack(alignment: .leading, spacing: DesignTokens.summaryGroupSpacing) {
                        section(title: "已定", items: summary.decided, color: DesignTokens.okGreen)
                        section(title: "进行中", items: summary.inProgress, color: DesignTokens.blue)
                        section(title: "待拍板", items: summary.pending, color: DesignTokens.amber)
                    }
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

    private func tag(_ title: String, color: Color) -> some View {
        Text(title)
            .font(DesignTokens.summarySectionFont)
            .foregroundColor(color)
            .padding(.horizontal, DesignTokens.summaryTagHorizontal)
            .padding(.vertical, DesignTokens.summaryTagVertical)
            .background(color.opacity(DesignTokens.summaryTagOpacity), in: Capsule())
    }

    @ViewBuilder
    private func section(title: String, items: [String], color: Color) -> some View {
        if !items.isEmpty {
            HStack(alignment: .top, spacing: DesignTokens.shellGap) {
                tag(title, color: color)
                    .frame(width: DesignTokens.summaryLabelWidth, alignment: .leading)
                VStack(alignment: .leading, spacing: DesignTokens.summaryItemSpacing) {
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
}

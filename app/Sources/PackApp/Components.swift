import SwiftUI
import AppKit
import UniformTypeIdentifiers
import WebKit

enum PackStyle {
    static let accent = Color(red: 27.0 / 255, green: 124.0 / 255, blue: 245.0 / 255)
    static let amber = Color(red: 0.78, green: 0.48, blue: 0.12)
    static let canvas = Color(nsColor: .textBackgroundColor)
    static let surface = Color(nsColor: .controlBackgroundColor)
    static let line = Color.primary.opacity(0.075)
    static func color(_ hex: String?) -> Color {
        guard let hex else { return accent }
        let s = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        guard s.count == 6, let n = UInt64(s, radix: 16) else { return accent }
        return Color(red: Double((n >> 16) & 255) / 255, green: Double((n >> 8) & 255) / 255, blue: Double(n & 255) / 255)
    }
    static func status(_ status: String) -> Color {
        switch status {
        case "open", "released": return .gray
        case "claimed", "working": return accent
        case "review": return .orange
        case "done": return .green
        case "blocked", "cancelled": return .red
        default: return .secondary
        }
    }
}

struct SidebarMaterial: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = .sidebar
        view.blendingMode = .behindWindow
        view.state = .active
        return view
    }
    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {}
}

struct CardSurface: ViewModifier {
    var tint: Color = PackStyle.line
    func body(content: Content) -> some View {
        content.background(PackStyle.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(tint, lineWidth: 1))
            .shadow(color: .black.opacity(0.035), radius: 5, y: 2)
    }
}
extension View {
    func card(_ color: Color = PackStyle.line) -> some View { modifier(CardSurface(tint: color)) }
}

struct Pill: View {
    let title: String
    var color: Color = .secondary
    var body: some View {
        Text(title).font(.system(size: 11, weight: .semibold))
            .foregroundStyle(color).padding(.horizontal, 8).padding(.vertical, 4)
            .background(color.opacity(0.12), in: Capsule())
    }
}

enum QuotaDisplay {
    private static func date(_ timestamp: Double, format: String) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.timeZone = .current
        formatter.dateFormat = format
        return formatter.string(from: Date(timeIntervalSince1970: timestamp))
    }

    static func resetDate(_ timestamp: Double) -> String { date(timestamp, format: "M月d日") }
    static func updatedDate(_ timestamp: Double) -> String { date(timestamp, format: "MM/dd HH:mm") }
    static func tokens(_ value: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(value), countStyle: .decimal)
            .replacingOccurrences(of: "B", with: "")
            .trimmingCharacters(in: .whitespaces)
    }

    static func freshness(_ member: MemberInfo?) -> String? {
        guard let member else { return nil }
        if member.quotaUpdatedAt == nil {
            return member.quotaWeekUsedPct != nil || member.quotaExtraLeft != nil ? "数据时间未知" : nil
        }
        return member.quotaIsStale ? "已过期" : nil
    }
}

struct UrgentBadge: View {
    var body: some View {
        Text("加急").font(DesignTokens.urgentFont).foregroundStyle(DesignTokens.urgentText)
            .padding(.horizontal, DesignTokens.urgentHorizontal).padding(.vertical, DesignTokens.urgentVertical)
            .background(DesignTokens.urgentBackground, in: Capsule())
    }
}

struct MemberAvatarView: View {
    let name: String
    let member: MemberInfo?
    var size: CGFloat = 34
    var showStatus = true
    var working = false

    private var designedImage: NSImage? {
        guard (name == "dispatcher" || name == "absent"),
              !(UserDefaults.standard.stringArray(forKey: "pack.customAvatarNames") ?? []).contains(name),
              let url = Bundle.main.url(forResource: name, withExtension: "webp", subdirectory: "avatars") else { return nil }
        return NSImage(contentsOf: url)
    }

    var body: some View {
        let avatar = member?.avatar
        let color = DesignTokens.avatarPalettes[avatar?.palette ?? ""] ?? PackStyle.color(avatar?.c1)
        let background = DesignTokens.avatarBackgrounds[avatar?.bg ?? ""] ?? PackStyle.color(avatar?.c2)
        ZStack(alignment: .bottomTrailing) {
            Group {
                if let image = designedImage {
                    Image(nsImage: image).resizable().scaledToFill().clipShape(Circle())
                } else {
                    ParameterAvatar(species: avatar?.species ?? "fox", color: color, background: background, accessory: avatar?.accessory ?? "none")
                }
            }.frame(width: size, height: size).clipShape(Circle())
            if showStatus {
                ZStack {
                    if working { ActiveStatusPulse() }
                    Circle().fill(working ? DesignTokens.blue : (member?.isOnline == true ? Color.green : Color.gray))
                }.frame(width: max(8, size * 0.27), height: max(8, size * 0.27))
                    .overlay(Circle().stroke(PackStyle.canvas, lineWidth: 2))
                    .offset(x: 1, y: 1)
            }
        }.frame(width: size, height: size)
    }
}

final class PulseModel: ObservableObject {
    @Published var expanded = false
}

struct ActiveStatusPulse: View {
    @StateObject private var model = PulseModel()
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var body: some View {
        Circle().fill(DesignTokens.blue.opacity(DesignTokens.activePulseOpacity))
            .scaleEffect(model.expanded ? DesignTokens.activePulseMax : DesignTokens.activePulseMin)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(DesignTokens.activePulseAnimation) { model.expanded = true }
            }
    }
}

/// Local vector placeholder driven by the member's registered avatar combination.
struct ParameterAvatar: View {
    let species: String
    let color: Color
    let background: Color
    let accessory: String
    private var pointed: Bool { ["fox", "wolf", "cat", "tiger", "owl", "raccoon"].contains(species) }
    var body: some View {
        Canvas { context, size in
            let w = size.width, h = size.height
            func rect(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, _ height: CGFloat) -> CGRect {
                CGRect(x: x*w, y: y*h, width: width*w, height: height*h)
            }
            context.fill(Path(ellipseIn: rect(0, 0, 1, 1)), with: .color(background))
            if pointed {
                var ears = Path()
                ears.move(to: CGPoint(x: 0.18*w, y: 0.48*h)); ears.addLine(to: CGPoint(x: 0.20*w, y: 0.14*h)); ears.addLine(to: CGPoint(x: 0.43*w, y: 0.32*h))
                ears.move(to: CGPoint(x: 0.57*w, y: 0.32*h)); ears.addLine(to: CGPoint(x: 0.80*w, y: 0.14*h)); ears.addLine(to: CGPoint(x: 0.82*w, y: 0.48*h))
                context.fill(ears, with: .color(color))
            } else {
                context.fill(Path(ellipseIn: rect(0.14, 0.17, 0.27, 0.3)), with: .color(color))
                context.fill(Path(ellipseIn: rect(0.59, 0.17, 0.27, 0.3)), with: .color(color))
            }
            context.fill(Path(ellipseIn: rect(0.16, 0.25, 0.68, 0.63)), with: .color(color))
            context.fill(Path(ellipseIn: rect(0.31, 0.57, 0.38, 0.22)), with: .color(.white.opacity(0.75)))
            for x: CGFloat in [0.34, 0.61] { context.fill(Path(ellipseIn: rect(x, 0.47, 0.06, 0.07)), with: .color(DesignTokens.ink)) }
            context.fill(Path(ellipseIn: rect(0.47, 0.61, 0.06, 0.05)), with: .color(DesignTokens.ink))
            if accessory == "glasses" || accessory == "headset" {
                for x: CGFloat in [0.27, 0.54] { context.stroke(Path(ellipseIn: rect(x, 0.41, 0.19, 0.19)), with: .color(DesignTokens.ink), lineWidth: max(1, w * 0.025)) }
            } else if accessory != "none" {
                context.fill(Path(ellipseIn: rect(0.67, 0.18, 0.15, 0.15)), with: .color(DesignTokens.blue))
            }
        }.accessibilityLabel("参数化头像：\(species)")
    }
}


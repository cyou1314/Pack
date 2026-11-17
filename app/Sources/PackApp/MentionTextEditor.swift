import SwiftUI
import AppKit

/// Mention styling is visual only; the bound draft remains a plain string.
struct MentionTextEditor: NSViewRepresentable {
    @ObservedObject var state: AppState
    func makeCoordinator() -> Coordinator { Coordinator(state: state) }
    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = false
        scroll.borderType = .noBorder
        let editor = MentionNSTextView()
        editor.delegate = context.coordinator
        editor.isRichText = false
        editor.isVerticallyResizable = true
        editor.isHorizontallyResizable = false
        editor.drawsBackground = false
        editor.textContainerInset = NSSize(width: DesignTokens.mentionTextInset, height: DesignTokens.mentionTextInset)
        editor.textContainer?.lineFragmentPadding = DesignTokens.mentionTextInset
        editor.textContainer?.widthTracksTextView = true
        editor.font = NSFont.systemFont(ofSize: DesignTokens.mentionFontSize)
        editor.textColor = NSColor(DesignTokens.ink)
        scroll.documentView = editor
        context.coordinator.editor = editor
        return scroll
    }
    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let editor = scroll.documentView as? MentionNSTextView else { return }
        context.coordinator.state = state
        editor.placeholder = state.connected ? "发消息或派活，@ 成员指派" : "连接恢复后可发送"
        if editor.string != state.draftMessage && !editor.hasMarkedText() {
            editor.string = state.draftMessage
            editor.setSelectedRange(NSRange(location: (editor.string as NSString).length, length: 0))
        }
        context.coordinator.styleMentions()
        editor.needsDisplay = true
        if context.coordinator.lastFocusRequest != state.composerFocusRequest {
            context.coordinator.lastFocusRequest = state.composerFocusRequest
            DispatchQueue.main.async { editor.window?.makeFirstResponder(editor) }
        }
    }
    func sizeThatFits(_ proposal: ProposedViewSize, nsView: NSScrollView, context: Context) -> CGSize? {
        guard let width = proposal.width, let editor = nsView.documentView as? MentionNSTextView,
              let layout = editor.layoutManager, let container = editor.textContainer, let font = editor.font else { return nil }
        container.containerSize = NSSize(width: width, height: .greatestFiniteMagnitude)
        layout.ensureLayout(for: container)
        let line = layout.defaultLineHeight(for: font)
        let used = layout.usedRect(for: container).height
        let extraLine = editor.string.hasSuffix("\n") ? line : 0
        let height = min(max(used + extraLine, line * CGFloat(DesignTokens.composerMinLines)), line * CGFloat(DesignTokens.composerMaxLines))
        return CGSize(width: width, height: ceil(height))
    }
    final class Coordinator: NSObject, NSTextViewDelegate {
        var state: AppState
        weak var editor: MentionNSTextView?
        var lastFocusRequest = 0
        init(state: AppState) { self.state = state }
        func textDidChange(_ notification: Notification) {
            guard let editor else { return }
            state.draftMessage = editor.string
            if !editor.hasMarkedText() { styleMentions() }
            editor.invalidateIntrinsicContentSize()
        }
        func textView(_ textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
            if commandSelector == #selector(NSResponder.cancelOperation(_:)) {
                state.dismissMentions(); return true
            }
            if commandSelector == #selector(NSResponder.insertNewline(_:)), !textView.hasMarkedText(),
               !(NSApp.currentEvent?.modifierFlags.contains(.shift) ?? false) {
                state.submitComposer(); return true
            }
            return false
        }
        @MainActor func styleMentions() {
            guard let editor, !editor.hasMarkedText(), let storage = editor.textStorage else { return }
            let text = editor.string
            let length = (text as NSString).length
            let selection = editor.selectedRange()
            let font = NSFont.systemFont(ofSize: DesignTokens.mentionFontSize)
            let ordinary: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor(DesignTokens.ink)]
            storage.beginEditing()
            storage.setAttributes(ordinary, range: NSRange(location: 0, length: length))
            for member in state.members where member.role == "member" && member.name != "owner" {
                let escaped = NSRegularExpression.escapedPattern(for: member.name)
                guard let regex = try? NSRegularExpression(pattern: "(?<![\\p{L}\\p{N}_])@" + escaped + "(?=$|[\\s\\p{P}])") else { continue }
                for match in regex.matches(in: text, range: NSRange(location: 0, length: length)) {
                    storage.addAttributes([.foregroundColor: NSColor(DesignTokens.blue), .backgroundColor: NSColor(DesignTokens.blue).withAlphaComponent(DesignTokens.mentionBadgeOpacity)], range: match.range)
                }
            }
            storage.endEditing()
            editor.typingAttributes = ordinary
            if NSMaxRange(selection) <= length { editor.setSelectedRange(selection) }
        }
    }
}
final class MentionNSTextView: NSTextView {
    var placeholder = ""
    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        if string.isEmpty {
            let attributes: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: DesignTokens.mentionFontSize), .foregroundColor: NSColor.placeholderTextColor]
            (placeholder as NSString).draw(at: NSPoint(x: DesignTokens.mentionTextInset, y: DesignTokens.mentionTextInset), withAttributes: attributes)
        }
    }
}

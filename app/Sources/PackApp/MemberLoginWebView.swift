import SwiftUI
import WebKit

struct MemberLoginWebView: NSViewRepresentable {
    let memberName: String
    let url: URL
    let onAuthenticated: () -> Void
    let onError: (String) -> Void

    private static let providers: [String: URL] = ["muse": URL(string: "https://muse.ai")!]
    private static let identifiersKey = "pack.memberWebStores"

    static func startURL(provider: String?) -> URL? {
        let value = provider?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return providers[value.isEmpty ? "muse" : value]
    }

    static func storeIdentifier(for name: String) -> UUID {
        let defaults = UserDefaults.standard
        var ids = defaults.dictionary(forKey: identifiersKey) as? [String: String] ?? [:]
        if let value = ids[name], let id = UUID(uuidString: value) { return id }
        let id = UUID()
        ids[name] = id.uuidString
        defaults.set(ids, forKey: identifiersKey)
        return id
    }

    @MainActor
    static func removeStore(for name: String) async throws -> Bool {
        let defaults = UserDefaults.standard
        var ids = defaults.dictionary(forKey: identifiersKey) as? [String: String] ?? [:]
        guard let value = ids[name], let id = UUID(uuidString: value) else { return false }
        try await WKWebsiteDataStore.remove(forIdentifier: id)
        ids.removeValue(forKey: name)
        defaults.set(ids, forKey: identifiersKey)
        return true
    }

    func makeCoordinator() -> Coordinator { Coordinator(onAuthenticated: onAuthenticated, onError: onError) }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = WKWebsiteDataStore(forIdentifier: Self.storeIdentifier(for: memberName))
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.load(URLRequest(url: url))
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        private let onAuthenticated: () -> Void
        private let onError: (String) -> Void
        private var sawLogin = false
        private var notified = false

        init(onAuthenticated: @escaping () -> Void, onError: @escaping (String) -> Void) {
            self.onAuthenticated = onAuthenticated
            self.onError = onError
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            guard let url = webView.url else { return }
            let components = url.path.lowercased().split(separator: "/")
            let isLogin = components.contains { ["login", "sign-in", "signin", "auth", "oauth", "authorize"].contains(String($0)) }
            if isLogin { sawLogin = true; return }
            guard sawLogin, !notified, url.scheme?.lowercased() == "https", url.host?.lowercased() == "muse.ai" else { return }
            notified = true
            onAuthenticated()
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            onError("登录页面加载失败：\(error.localizedDescription)")
        }

        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if navigationAction.targetFrame == nil, let url = navigationAction.request.url, ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
                webView.load(navigationAction.request)
            }
            return nil
        }
    }
}

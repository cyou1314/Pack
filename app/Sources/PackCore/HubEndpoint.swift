import Foundation

/// Validates the Hub endpoint before any credential-bearing request is sent.
public enum HubEndpoint {
    public static func validatedURL(_ value: String) -> URL? {
        guard value == value.trimmingCharacters(in: .whitespacesAndNewlines),
              let components = URLComponents(string: value),
              let scheme = components.scheme?.lowercased(),
              let host = components.host, !host.isEmpty,
              components.user == nil,
              components.password == nil,
              components.query == nil,
              components.fragment == nil,
              let url = components.url else {
            return nil
        }

        switch scheme {
        case "https":
            return url
        case "http" where isLoopbackHost(host):
            return url
        default:
            return nil
        }
    }

    public static func allowsRedirect(from original: URL, to destination: URL) -> Bool {
        guard let source = validatedURL(original.absoluteString),
              let target = validatedURL(destination.absoluteString),
              source.scheme?.lowercased() == target.scheme?.lowercased(),
              source.host?.lowercased() == target.host?.lowercased(),
              effectivePort(source) == effectivePort(target) else {
            return false
        }
        return true
    }

    private static func effectivePort(_ url: URL) -> Int? {
        if let port = url.port { return port }
        switch url.scheme?.lowercased() {
        case "https": return 443
        case "http": return 80
        default: return nil
        }
    }

    public static func isLoopbackHost(_ value: String) -> Bool {
        var host = value.lowercased()
        if host.hasPrefix("[") && host.hasSuffix("]") {
            host = String(host.dropFirst().dropLast())
        }
        if host.hasSuffix(".") {
            host.removeLast()
        }
        return host == "localhost" || host == "127.0.0.1" || host == "::1"
    }
}

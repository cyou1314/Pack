import Foundation

/// Prevents dispatcher credentials from being sent to arbitrary URLs.
/// Local installations use the fixed loopback Hub port; remote discovery is
/// restricted to the Pinggy hostname suffix used by Pack's address publisher.
public enum PackHubURLPolicy {
    public static func isAllowed(_ rawValue: String) -> Bool {
        guard let components = URLComponents(string: rawValue),
              let scheme = components.scheme?.lowercased(),
              let rawHost = components.host?.lowercased(),
              !rawHost.isEmpty,
              components.user == nil,
              components.password == nil,
              components.query == nil,
              components.fragment == nil,
              components.path == "/mcp" else {
            return false
        }

        let host = rawHost.trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        if scheme == "http" {
            return ["127.0.0.1", "localhost", "::1"].contains(host)
                && components.port == 8794
        }
        if scheme == "https" {
            let isPinggy = host.hasSuffix(".pinggy-free.link")
            return isPinggy && (components.port == nil || components.port == 443)
        }
        return false
    }
}

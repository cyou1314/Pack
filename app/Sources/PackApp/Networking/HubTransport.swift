import Foundation
import PackCore

private final class SameOriginHubRedirectDelegate: NSObject, URLSessionTaskDelegate {
    private let origin: URL

    init(origin: URL) {
        self.origin = origin
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        guard let destination = request.url,
              HubEndpoint.allowsRedirect(from: origin, to: destination) else {
            completionHandler(nil)
            return
        }
        completionHandler(request)
    }
}

/// Owns the credential-bearing HTTP boundary to the Hub.
///
/// Keep request construction and redirect policy here so the JSON-RPC client
/// can focus on MCP session and response semantics. Each request deliberately
/// retains the existing ephemeral-session behavior.
struct HubTransport {
    func post(
        config: PackConfig,
        sessionID: String?,
        body: [String: Any],
        isNotification: Bool
    ) async throws -> (Data, HTTPURLResponse) {
        guard let url = HubEndpoint.validatedURL(config.hubUrl) else {
            throw HubError.badResponse("Hub URL must use HTTPS except for loopback HTTP")
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 20
        request.httpShouldHandleCookies = false
        request.setValue("close", forHTTPHeaderField: "Connection")
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json, text/event-stream", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(config.token)", forHTTPHeaderField: "Authorization")
        if let sessionID {
            request.setValue(sessionID, forHTTPHeaderField: "mcp-session-id")
        }
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let redirectDelegate = SameOriginHubRedirectDelegate(origin: url)
        let session = URLSession(
            configuration: .ephemeral,
            delegate: redirectDelegate,
            delegateQueue: nil
        )
        defer { session.invalidateAndCancel() }

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw HubError.badResponse("no http response")
        }
        if isNotification { return (data, http) }
        if http.statusCode != 200 {
            throw HubError.badResponse("http \(http.statusCode)")
        }
        return (data, http)
    }
}

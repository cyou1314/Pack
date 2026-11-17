import Foundation

struct ReplySnapshot: Codable, Equatable {
    let author: String
    let text: String
}

enum ReplyCodec {
    private static let marker = "\n\n[pack-reply-v1:"
    static func encode(_ body: String, reply: ReplySnapshot?) -> String {
        guard let reply, let data = try? JSONEncoder().encode(reply) else { return body }
        let encoded = data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
        return body + marker + encoded + "]"
    }
    static func decode(_ wire: String) -> (body: String, reply: ReplySnapshot?) {
        guard wire.hasSuffix("]"), let range = wire.range(of: marker, options: .backwards) else { return (wire, nil) }
        let payload = String(wire[range.upperBound..<wire.index(before: wire.endIndex)])
        guard !payload.isEmpty, payload.range(of: "^[A-Za-z0-9_-]+$", options: .regularExpression) != nil else { return (wire, nil) }
        var base64 = payload.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        base64 += String(repeating: "=", count: (4 - base64.count % 4) % 4)
        guard let data = Data(base64Encoded: base64), let reply = try? JSONDecoder().decode(ReplySnapshot.self, from: data), !reply.author.isEmpty else { return (wire, nil) }
        return (String(wire[..<range.lowerBound]), reply)
    }
}

import Foundation

struct RoomMessage: Identifiable {
    let id: Int
    let author: String
    let kind: String
    let body: String
    let taskId: Int?
    let createdAt: Double
}

struct PackTask: Identifiable {
    let id: Int
    let title: String
    let body: String
    let status: String
    let assignee: String?
    let attempts: Int
    let result: String?
    let updatedAt: Double

    var statusLabel: String {
        switch status {
        case "open": return "待认领"
        case "claimed": return "已认领"
        case "working": return "进行中"
        case "review": return "待验收"
        case "done": return "已完成"
        case "blocked": return "卡住"
        case "released": return "已释放"
        case "cancelled": return "已取消"
        default: return status
        }
    }
}

struct MemberInfo: Identifiable {
    var id: String { name }
    let name: String
    let role: String
    let profile: String?
    let lastHeartbeat: Double?

    var isOnline: Bool {
        guard let hb = lastHeartbeat else { return false }
        return Date().timeIntervalSince1970 - hb < 90
    }
}

func intVal(_ v: Any?) -> Int {
    if let i = v as? Int { return i }
    if let d = v as? Double { return Int(d) }
    return 0
}

func doubleVal(_ v: Any?) -> Double {
    if let d = v as? Double { return d }
    if let i = v as? Int { return Double(i) }
    return 0
}

func optIntVal(_ v: Any?) -> Int? {
    if v == nil || v is NSNull { return nil }
    return intVal(v)
}

func optDoubleVal(_ v: Any?) -> Double? {
    if v == nil || v is NSNull { return nil }
    return doubleVal(v)
}

func optStringVal(_ v: Any?) -> String? {
    if v == nil || v is NSNull { return nil }
    return v as? String
}

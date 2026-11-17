import Foundation

struct RoomMessage: Identifiable {
    let id: Int
    let author: String
    let kind: String
    let body: String
    let taskId: Int?
    let createdAt: Double
    // 中枢增量（消息软删/编辑）已返回 deleted/edited；解析预写，呈现随 B1-5。
    var deleted: Bool = false
    var edited: Bool = false
}

/// 结构化交活信封（中枢 task_complete 的 envelope，task_list 以 JSON 文本
/// 或对象返回，envelopeVal 两种都吃）。呈现随 B1-4 任务详情。
struct TaskEnvelope {
    let verdict: String
    let summary: String
    let artifacts: [[String: Any]]
    let selfCheck: String?
    // 中枢口径：known_issues / followups 是字符串数组（不是单个字符串），
    // staging23 S2 契约演练实证——按 String 解析会永远读不到。
    let knownIssues: [String]
    let followups: [String]
}

struct PackTask: Identifiable {
    let id: Int
    let title: String
    let body: String
    let status: String
    let assignee: String?
    let attempts: Int
    let result: String?
    let leaseExpires: Double?
    let updatedAt: Double
    // 中枢已返回、此前未解析的字段（解析预写）：交活信封、失败类别、
    // 退避到期、排班时刻（sched_at 非空 = 排班卡，区别于失败退避）。
    var envelope: TaskEnvelope? = nil
    var failClass: String? = nil
    var nextClaimAt: Double? = nil
    var schedAt: Double? = nil
    var createdBy: String? = nil
    var reviewNote: String? = nil

    var isScheduled: Bool { schedAt != nil }
    var isRelayPending: Bool { status == "claimed" && leaseExpires == nil }

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

/// Hub-assigned designed avatar: monogram glyph over a two-stop gradient
/// (hex strings like "#F97316"), picked server-side by a hash of the name.
/// 中枢 H3 起 avatar JSON 另带参数化组合（species/palette/accessory/bg，
/// combo 为占用登记键），渲染随 B1-7；旧载荷无组合字段时全为 nil。
struct AvatarInfo {
    let glyph: String
    let c1: String
    let c2: String
    var species: String? = nil
    var palette: String? = nil
    var accessory: String? = nil
    var bg: String? = nil
    var combo: String? = nil
}

struct AssignedSkill: Identifiable {
    var id: String { name }
    let name: String
    let version: String
}

struct MemberInfo: Identifiable {
    var id: String { name }
    let name: String
    let role: String
    let profile: String?
    let avatar: AvatarInfo?
    let lastHeartbeat: Double?
    // 中枢 H1 额度字段（member_list 已返回，解析预写）：周已用 %、重置时刻、
    // 附加 token 剩余、数据更新时间。呈现随 §4 用量卡/成员详情。
    var quotaWeekUsedPct: Double? = nil
    var quotaWeekResetAt: Double? = nil
    var quotaExtraLeft: Int? = nil
    var quotaUpdatedAt: Double? = nil

    var isOnline: Bool {
        guard let hb = lastHeartbeat else { return false }
        return Date().timeIntervalSince1970 - hb < DesignTokens.memberOnlineSeconds
    }

    /// 周额度剩余 %（环心数字口径，定稿 §4）；无数据为 nil。
    var quotaRemainingPct: Double? {
        guard let used = quotaWeekUsedPct else { return nil }
        return max(0, 100 - used)
    }

    /// 额度数据过期（定稿 §11.4：>15 分钟未更新即过期转灰、过期数字不作
    /// 派活依据；中枢自动接力的候选口径与此一致，900 秒）。
    var quotaIsStale: Bool {
        guard let at = quotaUpdatedAt else { return true }
        return Date().timeIntervalSince1970 - at > 900
    }

    /// 额度耗尽：数据新鲜且周已用 ≥100%。
    var quotaExhausted: Bool {
        !quotaIsStale && (quotaWeekUsedPct ?? 0) >= 100
    }
}

/// 房间摘要卡（中枢 room_summary_get，定稿 §14.1）：三组字符串列表。
struct RoomSummary {
    var decided: [String] = []
    var inProgress: [String] = []
    var pending: [String] = []
    var updatedAt: Double? = nil
    var updatedBy: String? = nil
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

func avatarVal(_ v: Any?) -> AvatarInfo? {
    guard let d = v as? [String: Any],
          let glyph = d["glyph"] as? String,
          let c1 = d["c1"] as? String,
          let c2 = d["c2"] as? String else { return nil }
    return AvatarInfo(glyph: glyph, c1: c1, c2: c2,
                      species: d["species"] as? String,
                      palette: d["palette"] as? String,
                      accessory: d["accessory"] as? String,
                      bg: d["bg"] as? String,
                      combo: d["combo"] as? String)
}

func boolVal(_ v: Any?) -> Bool {
    if let b = v as? Bool { return b }
    if let n = v as? NSNumber { return n.boolValue }
    return false
}

/// task_list 的 envelope 在中枢是 TEXT 列：可能以 JSON 字符串到 App，
/// 也可能已被框架解成对象——两种都吃。字段对不齐（缺 verdict/summary）
/// 按无信封处理，不让坏数据卡住列表渲染。
func envelopeVal(_ v: Any?) -> TaskEnvelope? {
    var d: [String: Any]?
    if let obj = v as? [String: Any] {
        d = obj
    } else if let s = v as? String, !s.isEmpty,
              let obj = try? JSONSerialization.jsonObject(with: Data(s.utf8)) as? [String: Any] {
        d = obj
    }
    guard let d, let verdict = d["verdict"] as? String,
          let summary = d["summary"] as? String else { return nil }
    // 数组字段容错：正常是 [String]；万一遇到历史脏数据是单个字符串，
    // 非空则包成一条，别让整封信封读不出来。
    func strList(_ v: Any?) -> [String] {
        if let arr = v as? [String] { return arr }
        if let s = v as? String, !s.isEmpty { return [s] }
        return []
    }
    // self_check 中枢允许两种形态：字符串，或 {ran, evidence} 对象；
    // 对象形态拼成一行展示文本，别静默丢掉。
    var selfCheck: String? = d["self_check"] as? String
    if selfCheck == nil, let sc = d["self_check"] as? [String: Any] {
        var parts: [String] = []
        if let ran = sc["ran"] as? Bool { parts.append(ran ? "自检已跑" : "自检未跑") }
        if let ev = sc["evidence"] as? String, !ev.isEmpty { parts.append(ev) }
        selfCheck = parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
    return TaskEnvelope(verdict: verdict, summary: summary,
                        artifacts: (d["artifacts"] as? [[String: Any]]) ?? [],
                        selfCheck: selfCheck,
                        knownIssues: strList(d["known_issues"]),
                        followups: strList(d["followups"]))
}

/// room_summary_get 的返回 → RoomSummary。
func summaryVal(_ res: [String: Any]) -> RoomSummary {
    let s = (res["summary"] as? [String: Any]) ?? [:]
    func strs(_ k: String) -> [String] { (s[k] as? [String]) ?? [] }
    return RoomSummary(decided: strs("decided"), inProgress: strs("in_progress"),
                       pending: strs("pending"),
                       updatedAt: optDoubleVal(res["updated_at"]),
                       updatedBy: res["updated_by"] as? String)
}

import Foundation

/// Tracks only snapshots actually fetched by the app; the first snapshot per room is a baseline.
struct NotificationDiff {
    private var roomApprovals: [String: Set<Int>] = [:]
    private var roomReviews: [String: Set<Int>] = [:]
    private var armedHeartbeats: [String: Double] = [:]

    mutating func changes(room: String, approvals: Set<Int>, reviews: Set<Int>, heartbeats: [String: Double], now: Double, onlineSeconds: TimeInterval, offlineSeconds: TimeInterval) -> (approvals: Set<Int>, reviews: Set<Int>, offline: Set<String>) {
        let newApprovals = roomApprovals[room].map { approvals.subtracting($0) } ?? []
        let newReviews = roomReviews[room].map { reviews.subtracting($0) } ?? []
        roomApprovals[room] = approvals
        roomReviews[room] = reviews
        var offline: Set<String> = []
        for (name, heartbeat) in heartbeats {
            let age = now - heartbeat
            if age < onlineSeconds {
                armedHeartbeats[name] = heartbeat
            } else if age >= offlineSeconds, armedHeartbeats[name] != nil {
                offline.insert(name)
                armedHeartbeats.removeValue(forKey: name)
            }
        }
        armedHeartbeats = armedHeartbeats.filter { heartbeats[$0.key] != nil }
        return (newApprovals, newReviews, offline)
    }
}

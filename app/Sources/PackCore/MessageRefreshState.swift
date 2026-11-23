import Foundation

/// Tracks when a room's latest-message snapshot was last applied successfully.
/// Room and Hub-client generations are separate so a failed first refresh after
/// switching rooms or reconnecting cannot reuse another context's cursor.
public struct MessageRefreshState: Equatable {
    public private(set) var lastFullSnapshotUptime: TimeInterval?
    public private(set) var lastClientGeneration = -1
    public private(set) var lastRoomGeneration = -1
    public private(set) var refreshRequestGeneration = 0

    public init() {}

    public func requiresFullSnapshot(
        roomLoading: Bool,
        force: Bool,
        now: TimeInterval,
        clientGeneration: Int,
        roomGeneration: Int,
        interval: TimeInterval = 60
    ) -> Bool {
        guard !roomLoading, !force,
              let lastFullSnapshotUptime,
              lastClientGeneration == clientGeneration,
              lastRoomGeneration == roomGeneration,
              now >= lastFullSnapshotUptime,
              now - lastFullSnapshotUptime < interval else {
            return true
        }
        return false
    }

    public mutating func invalidateSnapshot() {
        lastFullSnapshotUptime = nil
        lastClientGeneration = -1
        lastRoomGeneration = -1
    }

    public mutating func beginRefresh() -> Int {
        refreshRequestGeneration += 1
        return refreshRequestGeneration
    }

    public func isCurrentRefresh(_ generation: Int) -> Bool {
        refreshRequestGeneration == generation
    }

    public mutating func recordFullSnapshot(
        at uptime: TimeInterval,
        clientGeneration: Int,
        roomGeneration: Int
    ) {
        lastFullSnapshotUptime = uptime
        lastClientGeneration = clientGeneration
        lastRoomGeneration = roomGeneration
    }

    /// A full page may be truncated. Continue from its last ID until a short
    /// page is received; the caller falls back to a latest-tail snapshot if
    /// the bounded page budget is exhausted.
    public static func needsAnotherPage(receivedCount: Int, pageLimit: Int = 200) -> Bool {
        receivedCount >= pageLimit
    }

    public static let incrementalPageLimit = 200
    public static let maximumIncrementalPages = 5
}

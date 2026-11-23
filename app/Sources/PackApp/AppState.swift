import SwiftUI
import Security
import PackCore

@MainActor
final class AppState: ObservableObject {
    @Published var messages: [RoomMessage] = []
    @Published var tasks: [PackTask] = []
    @Published var members: [MemberInfo] = []
    @Published var connectionNote: String = "未连接"
    @Published var connected: Bool = false
    @Published var connectionFailed: Bool = false
    @Published var roomSummary: RoomSummary?
    @Published var roomLoading = true
    @Published var unreadCounts: [String: Int] = [:]
    @Published var summaryExpanded = true
    @Published var showAddMenu = false
    @Published var showSearch = false
    @Published var notificationStatus = "正在检查系统通知状态"
    @Published var selection: String? = "room"
    @Published var selectedTaskId: Int?
    @Published var boardFilter: String = "全部"
    @Published var highlightedMessageId: Int?
    @Published var expandedChatTaskId: Int?
    @Published var reviewInputTaskId: Int?
    @Published var reviewInput: String = ""
    @Published var reviewNotes: [Int: String] = [:]
    @Published var taskEvents: [[String: Any]] = []
    @Published var taskEventsTruncated = false
    @Published var taskRuns: [[String: Any]] = []
    @Published var taskTransfers: [[String: Any]] = []
    @Published var detailLoadNote: String = ""
    @Published var hoveredMessageId: Int?
    @Published var editingMessageId: Int?
    @Published var editingMessageText = ""
    @Published var currentRoom = "pack-main"
    @Published var rooms: [RoomItem] = []
    @Published var roomDrafts: [String: String] = UserDefaults.standard.dictionary(forKey: "pack.roomDrafts") as? [String: String] ?? [:]
    @Published var showRoomDialog = false
    @Published var roomDialogName = ""
    @Published var roomRenameFrom: String?
    @Published var roomDialogError = ""
    @Published var memberRunDurations: [Int: TimeInterval] = [:]
    @Published var memberRunsLoading = false
    @Published var memberSkills: [AssignedSkill] = []
    @Published var memberSkillsStatus = ""
    @Published var showAvatarPicker = false
    @Published var avatarRevision = 0
    @Published var claimingMembers: Set<String> = []
    @Published var hoveredMenuItem: String?
    @Published var avatarTargetName = ""
    @Published var avatarSaving = false
    @Published var avatarSaveError = ""
    @Published var editAvatarSpecies = "fox"
    @Published var editAvatarPalette = "orange"
    @Published var editAvatarAccessory = "none"
    @Published var editAvatarBackground = "cream"
    @Published var decisionsPending: Set<Int> = []
    @Published var decisionErrors: [Int: String] = [:]
    @Published var questionInputs: [Int: String] = [:]
    @Published var questionSending: Set<Int> = []
    @Published var questionErrors: [Int: String] = [:]
    @Published var questionResponderName: String?
    @Published var localQuestionAnswers: [Int: (author: String, body: String)] = [:]
    @Published var alwaysRuleInFlight: Set<Int> = []
    @Published var alwaysRuleErrors: [Int: String] = Dictionary(uniqueKeysWithValues:
        (UserDefaults.standard.dictionary(forKey: "pack.alwaysRuleErrors") ?? [:]).compactMap { key, value in
            guard let id = Int(key), let text = value as? String else { return nil }
            return (id, text)
        }) {
        didSet { UserDefaults.standard.set(Dictionary(uniqueKeysWithValues: alwaysRuleErrors.map { (String($0.key), $0.value) }), forKey: "pack.alwaysRuleErrors") }
    }
    @Published var alwaysRuleSucceeded = Set(UserDefaults.standard.array(forKey: "pack.alwaysRuleSucceeded") as? [Int] ?? []) {
        didSet { UserDefaults.standard.set(Array(alwaysRuleSucceeded), forKey: "pack.alwaysRuleSucceeded") }
    }
    @Published var memberRunsFailed: Set<Int> = []
    @Published var selectedMemberName: String?
    @Published var showAddMember = false
    @Published var memberNameInput = ""
    @Published var memberActionError = ""
    @Published var avatarSpecies = "fox"
    @Published var avatarPalette = "orange"
    @Published var avatarAccessory = "none"
    @Published var avatarBackground = "cream"
    @Published var hoveredRoom: String?
    @Published var expandedMessages: Set<Int> = []
    @Published var lastSeenByRoom: [String: Int] = UserDefaults.standard.dictionary(forKey: "pack.lastSeen") as? [String: Int] ?? [:]
    @Published var unreadBoundary: Int?
    @Published var showCatalog = false
    @Published var catalogKind = ""
    @Published var catalogItems: [[String: Any]] = []
    @Published var catalogNote = ""
    @Published var catalogArgument = ""
    @Published var attachmentsByRoom: [String: [PendingAttachment]] = [:]
    @Published var attachmentNoticeByRoom: [String: String] = [:]
    @Published var attachmentImages: [Int: NSImage] = [:]
    @Published var attachmentFailures: Set<Int> = []
    var attachmentLoading: Set<Int> = []
    var attachments: [PendingAttachment] { attachmentsByRoom[currentRoom] ?? [] }
    @Published var artifactPreviewURL: URL?
    @Published var showArtifactPreview = false
    @Published var selectedArtifacts: [Int: Int] = [:]
    @Published var showMemberLogin = false
    @Published var loginTargetName = ""
    @Published var loginTargetProvider: String?
    @Published var memberLoginSaving = false
    @Published var memberHandoffInProgress: String?
    @Published var memberHandoffResults: [String: String] = UserDefaults.standard.dictionary(forKey: "pack.memberHandoffResults") as? [String: String] ?? [:]
    @Published var memberRemovalInProgress = false
    @Published var memberRemovalReport = UserDefaults.standard.string(forKey: "pack.memberRemovalReport") ?? ""
    @Published var showRemoveMember = false
    @Published var pendingMembers: [String] = UserDefaults.standard.stringArray(forKey: "pack.pendingMembers") ?? []
    @Published var draftMessage: String = "" {
        didSet {
            roomDrafts[currentRoom] = draftMessage
            UserDefaults.standard.set(roomDrafts, forKey: "pack.roomDrafts")
        }
    }
    @Published var replyByRoom: [String: ReplySnapshot] = [:]
    @Published var sendingReply: ReplySnapshot?
    @Published var sendingMessage: String?
    @Published var sendingRoom: String?
    @Published var sendingFailedID: UUID?
    @Published var failedMessagesByRoom: [String: [FailedChatMessage]] = [:]
    @Published var failedMessageNotices: [UUID: String] = [:]
    /// Draft text at the moment the user dismissed the @-mention popup
    /// (Esc / click elsewhere); the popup stays hidden while the draft
    /// still equals this, and reappears as soon as the text changes.
    @Published var mentionDismissedText: String?
    @Published var composerFocusRequest = 0
    @Published var showCreateTask: Bool = false
    @Published var showInlineTask: Bool = false
    @Published var inlineTaskSending = false
    @Published var inlineTaskError = ""
    @Published var showSidebar: Bool = true
    @Published var sidebarPinned: Bool = false
    @Published var roomExpanded: Bool = true
    @Published var newTitle: String = ""
    @Published var newBody: String = ""
    @Published var newAssignee: String = "absent"
    @Published var newNeedsApproval: Bool = false
    @Published var newUrgent = false
    @Published var urgentUpdating: Set<Int> = []
    @Published var urgentErrors: [Int: String] = [:]
    @Published var rejectReasons: [Int: String] = [:]
    @Published var lastActionNote: String = ""
    @Published var settingsHubURL = ""
    @Published var settingsMessage = ""
    @Published var settingsAddrToken = ""
    @Published var addrTokenStatus = "地址源令牌未配置"
    @Published var credentialStatus = "正在检查凭证…"
    @Published var showOnboarding = false
    @Published var approvalDefault: Bool = UserDefaults.standard.bool(forKey: "pack.approvalDefault") {
        didSet { UserDefaults.standard.set(approvalDefault, forKey: "pack.approvalDefault") }
    }
    @Published var appRefreshSeconds: Int = (UserDefaults.standard.integer(forKey: "pack.appRefreshSeconds") > 0 ? UserDefaults.standard.integer(forKey: "pack.appRefreshSeconds") : 15) {
        didSet { UserDefaults.standard.set(appRefreshSeconds, forKey: "pack.appRefreshSeconds") }
    }

    var client: HubClient?
    var ownerClient: HubClient?
    var pollTask: Task<Void, Never>?
    var storedHubURL = ""
    var manualHubURLSaved = false
    var lastHubDiscoveryUptime: TimeInterval?
    var discoveryInProgress = false
    var clientGeneration = 0
    var consecutiveRefreshFailures = 0
    let hubDiscoveryInterval: TimeInterval = 60
    let hubDiscoveryTimeout: TimeInterval = 5
    let taskListPageSize = 100
    let taskListMaxPages = 10
    var roomGeneration = 0
    var unreadTask: Task<Void, Never>?
    var notificationDiff = NotificationDiff()
    var decisionRooms: [Int: String] = [:]
    var pendingNotification: (kind: String, room: String, id: Int?, member: String?)?

    // MARK: - derived

    var selectedTask: PackTask? { tasks.first { $0.id == selectedTaskId } }

    // MARK: - actions

    func act(_ note: String, _ op: @escaping (HubClient) async throws -> [String: Any]) {
        guard let client else { return }
        Task {
            do {
                _ = try await op(client)
                lastActionNote = note
                await refresh()
            } catch {
                lastActionNote = "操作未完成，请检查连接后重试"
            }
        }
    }


}

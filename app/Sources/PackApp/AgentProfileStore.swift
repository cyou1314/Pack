import Foundation
import CSQLite

struct AgentConversation: Codable, Hashable, Identifiable, Sendable {
    let id: UUID
    let agentID: UUID
    var title: String
    let createdAt: Date
    var updatedAt: Date
}

struct AgentChatMessage: Codable, Hashable, Identifiable, Sendable {
    let id: UUID
    let agentID: UUID
    let conversationID: UUID
    let id: UUID
    let agentID: UUID
    let role: String
    let content: String
    let createdAt: Date
}

enum AgentProfileStoreError: Error {
    case sqlite(String)
    case unsupportedSchemaVersion(Int32)
    case invalidEncoding
}

private final class SQLiteConnection {
    private var handle: OpaquePointer?

    init(url: URL) throws {
        let result = sqlite3_open_v2(
            url.path,
            &handle,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX,
            nil
        )
        guard result == SQLITE_OK, handle != nil else {
            let message = handle.map { String(cString: sqlite3_errmsg($0)) } ?? "open failed"
            if let handle { sqlite3_close_v2(handle) }
            throw AgentProfileStoreError.sqlite(message)
        }
    }

    deinit {
        if let handle { sqlite3_close_v2(handle) }
    }

    var db: OpaquePointer {
        guard let handle else { fatalError("SQLite connection is closed") }
        return handle
    }

    func execute(_ sql: String) throws {
        var errorMessage: UnsafeMutablePointer<CChar>?
        let result = sqlite3_exec(db, sql, nil, nil, &errorMessage)
        guard result == SQLITE_OK else {
            let message = errorMessage.map { String(cString: $0) } ?? String(cString: sqlite3_errmsg(db))
            sqlite3_free(errorMessage)
            throw AgentProfileStoreError.sqlite(message)
        }
    }

    func prepare(_ sql: String) throws -> OpaquePointer {
        var statement: OpaquePointer?
        let result = sqlite3_prepare_v2(db, sql, -1, &statement, nil)
        guard result == SQLITE_OK, let statement else {
            throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(db)))
        }
        return statement
    }

    func schemaVersion() throws -> Int32 {
        let statement = try prepare("PRAGMA user_version;")
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(db)))
        }
        return sqlite3_column_int(statement, 0)
    }
}

/// Local-first profile persistence. Secrets remain in Keychain; SQLite stores
/// only serializable profile metadata and Keychain references.
actor AgentProfileStore {
    private let connection: SQLiteConnection
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(directoryURL: URL? = nil) throws {
        let directory: URL
        if let directoryURL {
            directory = directoryURL
        } else {
            directory = try FileManager.default.url(
                for: .applicationSupportDirectory,
                in: .userDomainMask,
                appropriateFor: nil,
                create: true
            ).appendingPathComponent("Pack", isDirectory: true)
        }
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        let databaseURL = directory.appendingPathComponent("agents.sqlite")
        let connection = try SQLiteConnection(url: databaseURL)
        self.connection = connection
        self.encoder = JSONEncoder()
        self.decoder = JSONDecoder()

        try connection.execute("PRAGMA journal_mode=WAL;")
        try connection.execute("PRAGMA synchronous=FULL;")
        try connection.execute("PRAGMA foreign_keys=ON;")
        try connection.execute("PRAGMA busy_timeout=5000;")

        let version = try connection.schemaVersion()
        if version == 0 {
            try connection.execute("""
                CREATE TABLE IF NOT EXISTS agent_profiles (
                    id TEXT PRIMARY KEY NOT NULL,
                    provider_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_profiles_provider
                    ON agent_profiles(provider_id);
                PRAGMA user_version=1;
                """)
        } else if version != 1 && version != 2 {
            throw AgentProfileStoreError.unsupportedSchemaVersion(version)
        }
        if version < 2 {
            try connection.execute("""
                CREATE TABLE IF NOT EXISTS agent_chat_messages (
                    id TEXT PRIMARY KEY NOT NULL,
                    agent_id TEXT NOT NULL REFERENCES agent_profiles(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_chat_messages_agent_time
                    ON agent_chat_messages(agent_id, created_at, id);
                PRAGMA user_version=2;
                """)
        }
        if version < 3 {
            try connection.execute("""
                CREATE TABLE IF NOT EXISTS agent_conversations (
                    id TEXT PRIMARY KEY NOT NULL,
                    agent_id TEXT NOT NULL REFERENCES agent_profiles(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_conversations_agent_time
                    ON agent_conversations(agent_id, updated_at DESC);
                ALTER TABLE agent_chat_messages ADD COLUMN conversation_id TEXT
                    REFERENCES agent_conversations(id) ON DELETE CASCADE;
                """)
            let profiles = try allProfiles()
            for profile in profiles {
                let conversationID = UUID()
                let now = Date().timeIntervalSince1970
                let insert = try connection.prepare("INSERT INTO agent_conversations (id, agent_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?);")
                bindText(conversationID.uuidString, at: 1, to: insert)
                bindText(profile.id.uuidString, at: 2, to: insert)
                bindText("默认会话", at: 3, to: insert)
                sqlite3_bind_double(insert, 4, now)
                sqlite3_bind_double(insert, 5, now)
                let inserted = sqlite3_step(insert)
                sqlite3_finalize(insert)
                guard inserted == SQLITE_DONE else {
                    throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(connection.db)))
                }
                let update = try connection.prepare("UPDATE agent_chat_messages SET conversation_id=? WHERE agent_id=? AND conversation_id IS NULL;")
                bindText(conversationID.uuidString, at: 1, to: update)
                bindText(profile.id.uuidString, at: 2, to: update)
                let updated = sqlite3_step(update)
                sqlite3_finalize(update)
                guard updated == SQLITE_DONE else {
                    throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(connection.db)))
                }
            }
            try connection.execute("PRAGMA user_version=3;")
        }
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: databaseURL.path)
    }

    func add(_ profile: AgentProfile) throws {
        try profile.validate()
        let json = try encode(profile)
        let statement = try connection.prepare("""
            INSERT INTO agent_profiles
                (id, provider_id, mode, display_name, profile_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """)
        defer { sqlite3_finalize(statement) }
        try bind(profile, json: json, to: statement)
        let result = sqlite3_step(statement)
        guard result == SQLITE_DONE else {
            throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(connection.db)))
        }
    }

    func save(_ profile: AgentProfile) throws {
        try profile.validate()
        let json = try encode(profile)
        let statement = try connection.prepare("""
            INSERT INTO agent_profiles
                (id, provider_id, mode, display_name, profile_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                provider_id=excluded.provider_id,
                mode=excluded.mode,
                display_name=excluded.display_name,
                profile_json=excluded.profile_json,
                updated_at=excluded.updated_at;
            """)
        defer { sqlite3_finalize(statement) }
        try bind(profile, json: json, to: statement)
        let result = sqlite3_step(statement)
        guard result == SQLITE_DONE else {
            throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(connection.db)))
        }
    }

    func profile(id: UUID) throws -> AgentProfile? {
        let statement = try connection.prepare("SELECT profile_json FROM agent_profiles WHERE id=? LIMIT 1;")
        defer { sqlite3_finalize(statement) }
        bindText(id.uuidString, at: 1, to: statement)
        guard sqlite3_step(statement) == SQLITE_ROW else { return nil }
        guard let text = sqlite3_column_text(statement, 0) else {
            throw AgentProfileStoreError.invalidEncoding
        }
        let length = Int(sqlite3_column_bytes(statement, 0))
        let data = Data(bytes: text, count: length)
        return try decoder.decode(AgentProfile.self, from: data)
    }

    func allProfiles() throws -> [AgentProfile] {
        let statement = try connection.prepare("SELECT profile_json FROM agent_profiles ORDER BY created_at ASC;")
        defer { sqlite3_finalize(statement) }
        var result: [AgentProfile] = []
        while true {
            let step = sqlite3_step(statement)
            if step == SQLITE_DONE { break }
            guard step == SQLITE_ROW, let text = sqlite3_column_text(statement, 0) else {
                throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(connection.db)))
            }
            let length = Int(sqlite3_column_bytes(statement, 0))
            let data = Data(bytes: text, count: length)
            result.append(try decoder.decode(AgentProfile.self, from: data))
        }
        return result
    }

    func appendChatMessage(agentID: UUID, conversationID: UUID, role: String, content: String) throws {
        guard ["user", "assistant", "system"].contains(role) else {
            throw AgentProfileStoreError.sqlite("invalid chat message role")
        }
        guard !content.isEmpty else { return }
        let statement = try connection.prepare("""
            INSERT INTO agent_chat_messages (id, agent_id, role, content, created_at, conversation_id)
            VALUES (?, ?, ?, ?, ?, ?);
            """)
        defer { sqlite3_finalize(statement) }
        bindText(UUID().uuidString, at: 1, to: statement)
        bindText(agentID.uuidString, at: 2, to: statement)
        bindText(role, at: 3, to: statement)
        bindText(content, at: 4, to: statement)
        sqlite3_bind_double(statement, 5, Date().timeIntervalSince1970)
        bindText(conversationID.uuidString, at: 6, to: statement)
        guard sqlite3_step(statement) == SQLITE_DONE else {
            throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(connection.db)))
        }
    }

    func chatMessages(agentID: UUID, conversationID: UUID, limit: Int = 500) throws -> [AgentChatMessage] {
        let statement = try connection.prepare("""
            SELECT id, role, content, created_at
            FROM (
                SELECT id, role, content, created_at, rowid
                FROM agent_chat_messages
                WHERE agent_id=? AND conversation_id=?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, rowid ASC;
            """)
        defer { sqlite3_finalize(statement) }
        bindText(agentID.uuidString, at: 1, to: statement)
        bindText(conversationID.uuidString, at: 2, to: statement)
        sqlite3_bind_int(statement, 3, Int32(max(1, min(limit, 5000))))
        var result: [AgentChatMessage] = []
        while true {
            let step = sqlite3_step(statement)
            if step == SQLITE_DONE { break }
            guard step == SQLITE_ROW,
                  let idText = sqlite3_column_text(statement, 0),
                  let roleText = sqlite3_column_text(statement, 1),
                  let contentText = sqlite3_column_text(statement, 2) else {
                throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(connection.db)))
            }
            let createdAt = Date(timeIntervalSince1970: sqlite3_column_double(statement, 3))
            result.append(AgentChatMessage(
                id: UUID(uuidString: String(cString: idText)) ?? UUID(),
                agentID: agentID,
                conversationID: conversationID,
                role: String(cString: roleText),
                content: String(cString: contentText),
                createdAt: createdAt
            ))
        }
        return result
    }

    func conversations(agentID: UUID) throws -> [AgentConversation] {
        let statement = try connection.prepare("SELECT id, title, created_at, updated_at FROM agent_conversations WHERE agent_id=? ORDER BY updated_at DESC;")
        defer { sqlite3_finalize(statement) }
        bindText(agentID.uuidString, at: 1, to: statement)
        var result: [AgentConversation] = []
        while true {
            let step = sqlite3_step(statement)
            if step == SQLITE_DONE { break }
            guard step == SQLITE_ROW, let idText = sqlite3_column_text(statement, 0), let titleText = sqlite3_column_text(statement, 1) else {
                throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(connection.db)))
            }
            result.append(AgentConversation(id: UUID(uuidString: String(cString: idText)) ?? UUID(), agentID: agentID, title: String(cString: titleText), createdAt: Date(timeIntervalSince1970: sqlite3_column_double(statement, 2)), updatedAt: Date(timeIntervalSince1970: sqlite3_column_double(statement, 3))))
        }
        return result
    }

    func createConversation(agentID: UUID, title: String) throws -> AgentConversation {
        guard try profile(id: agentID) != nil else { throw AgentProfileStoreError.sqlite("Agent profile not found") }
        let now = Date()
        let conversation = AgentConversation(id: UUID(), agentID: agentID, title: title, createdAt: now, updatedAt: now)
        let statement = try connection.prepare("INSERT INTO agent_conversations (id, agent_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?);")
        defer { sqlite3_finalize(statement) }
        bindText(conversation.id.uuidString, at: 1, to: statement)
        bindText(agentID.uuidString, at: 2, to: statement)
        bindText(title, at: 3, to: statement)
        sqlite3_bind_double(statement, 4, now.timeIntervalSince1970)
        sqlite3_bind_double(statement, 5, now.timeIntervalSince1970)
        guard sqlite3_step(statement) == SQLITE_DONE else { throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(connection.db))) }
        return conversation
    }

    func renameConversation(id: UUID, agentID: UUID, title: String) throws {
        let statement = try connection.prepare("UPDATE agent_conversations SET title=?, updated_at=? WHERE id=? AND agent_id=?;")
        defer { sqlite3_finalize(statement) }
        bindText(title, at: 1, to: statement)
        sqlite3_bind_double(statement, 2, Date().timeIntervalSince1970)
        bindText(id.uuidString, at: 3, to: statement)
        bindText(agentID.uuidString, at: 4, to: statement)
        guard sqlite3_step(statement) == SQLITE_DONE, sqlite3_changes(connection.db) > 0 else { throw AgentProfileStoreError.sqlite("Conversation not found") }
    }

    func removeConversation(id: UUID, agentID: UUID) throws {
        let statement = try connection.prepare("DELETE FROM agent_conversations WHERE id=? AND agent_id=?;")
        defer { sqlite3_finalize(statement) }
        bindText(id.uuidString, at: 1, to: statement)
        bindText(agentID.uuidString, at: 2, to: statement)
        guard sqlite3_step(statement) == SQLITE_DONE, sqlite3_changes(connection.db) > 0 else { throw AgentProfileStoreError.sqlite("Conversation not found") }
    }

    func clearChatMessages(agentID: UUID) throws {
        let statement = try connection.prepare("DELETE FROM agent_chat_messages WHERE agent_id=?;")
        defer { sqlite3_finalize(statement) }
        bindText(agentID.uuidString, at: 1, to: statement)
        guard sqlite3_step(statement) == SQLITE_DONE else {
            throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(connection.db)))
        }
    }

    @discardableResult
    func remove(id: UUID) throws -> Bool {
        let statement = try connection.prepare("DELETE FROM agent_profiles WHERE id=?;")
        defer { sqlite3_finalize(statement) }
        bindText(id.uuidString, at: 1, to: statement)
        let result = sqlite3_step(statement)
        guard result == SQLITE_DONE else {
            throw AgentProfileStoreError.sqlite(String(cString: sqlite3_errmsg(connection.db)))
        }
        return sqlite3_changes(connection.db) > 0
    }

    private func encode(_ profile: AgentProfile) throws -> String {
        guard let value = String(data: try encoder.encode(profile), encoding: .utf8) else {
            throw AgentProfileStoreError.invalidEncoding
        }
        return value
    }

    private func bind(_ profile: AgentProfile, json: String, to statement: OpaquePointer) throws {
        bindText(profile.id.uuidString, at: 1, to: statement)
        bindText(profile.providerID, at: 2, to: statement)
        bindText(profile.mode.rawValue, at: 3, to: statement)
        bindText(profile.displayName, at: 4, to: statement)
        bindText(json, at: 5, to: statement)
        sqlite3_bind_double(statement, 6, profile.createdAt.timeIntervalSince1970)
        sqlite3_bind_double(statement, 7, profile.updatedAt.timeIntervalSince1970)
    }

    private func bindText(_ value: String, at index: Int32, to statement: OpaquePointer) {
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        sqlite3_bind_text(statement, index, value, -1, transient)
    }
}

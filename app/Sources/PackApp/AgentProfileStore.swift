import Foundation
import CSQLite

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
        } else if version != 1 {
            throw AgentProfileStoreError.unsupportedSchemaVersion(version)
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
        return try decoder.decode(AgentProfile.self, from: Data(String(cString: text).utf8))
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
            result.append(try decoder.decode(AgentProfile.self, from: Data(String(cString: text).utf8)))
        }
        return result
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

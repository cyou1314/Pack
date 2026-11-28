import Foundation
import PackCore

/// Explicitly developer-only bundle resource. No environment override,
/// downloaded code or fallback to a globally installed Node executable.
enum NativeBrowserDevBundle {
    static var available: Bool {
        Bundle.main.object(forInfoDictionaryKey:"PackNativeBrowserDevelopment") as? Bool == true &&
        Bundle.main.resourceURL != nil
    }

    @MainActor static func open(account:AgentAccountSummary,supervisor:NativeLoginProcessSupervisor) async throws {
        guard available,!supervisor.phase.active,account.profile.mode == .nativeAccount,
              ["chatgpt","gemini","doubao"].contains(account.profile.providerID),
              let resources = Bundle.main.resourceURL else { throw AgentAccountSetupError.invalidConfiguration }
        let root = resources.appendingPathComponent("NativeBrowserCandidate",isDirectory:true)
        let node = root.appendingPathComponent("node/bin/node")
        let driver = root.appendingPathComponent("runtime/browser-driver",isDirectory:true)
        let cli = driver.appendingPathComponent("native-login-cli.mjs")
        let browser = root.appendingPathComponent("browsers",isDirectory:true)
        // Verify the bundle resource seal before launching the fixed program.
        // A developer's ad-hoc signature is an integrity check, not publisher trust.
        let bundle = Bundle.main.bundleURL
        let valid = await Task.detached {
            let process = Process()
            process.executableURL = URL(fileURLWithPath:"/usr/bin/codesign")
            process.arguments = ["--verify","--deep","--strict",bundle.path]
            process.environment = ["PATH":"/usr/bin:/bin"]
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            do { try process.run(); process.waitUntilExit() }
            catch { return false }
            return process.terminationReason == .exit && process.terminationStatus == 0
        }.value
        guard valid,!Task.isCancelled,!supervisor.phase.active,
              FileManager.default.isExecutableFile(atPath:node.path),
              FileManager.default.isReadableFile(atPath:cli.path)
        else { throw AgentAccountSetupError.invalidConfiguration }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        try supervisor.launch(accountID:account.id,executable:node,
            arguments:[cli.path,account.id.uuidString.lowercased(),"--managed-login"],
            directory:driver,environment:["HOME":home,"PATH":"/usr/bin:/bin","PLAYWRIGHT_BROWSERS_PATH":browser.path])
    }
}

import SwiftUI
import Security
import PackCore

struct RoomItem: Identifiable {
    let id: Int
    let name: String
    let lastMessageId: Int
}

import SwiftUI
import Security
import PackCore

struct RoomItem: Identifiable {
    let id: Int
    let name: String
    let lastMessageId: Int
}

@main
struct PackApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }.defaultSize(width: 1280, height: 820)
    }
}

// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "PackApp",
    platforms: [.macOS(.v14)],
    targets: [
        .systemLibrary(name: "CSQLite", path: "Sources/CSQLite"),
        .executableTarget(name: "PackApp", dependencies: ["CSQLite"], path: "Sources/PackApp")
    ]
)

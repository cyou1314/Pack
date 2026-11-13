// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "PackApp",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "PackApp", path: "Sources/PackApp")
    ]
)

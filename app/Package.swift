// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "PackApp",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "PackCore", path: "Sources/PackCore"),
        .executableTarget(name: "PackApp", dependencies: ["PackCore"], path: "Sources/PackApp"),
        .testTarget(name: "PackCoreTests", dependencies: ["PackCore"], path: "Tests/PackCoreTests")
    ]
)

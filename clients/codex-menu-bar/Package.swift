// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "CodexMenuBar",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "CodexMenuBar", targets: ["CodexMenuBar"]),
        .executable(name: "CodexMenuBarSnapshot", targets: ["CodexMenuBarSnapshot"]),
        .executable(name: "CodexProxyWidget", targets: ["CodexProxyWidget"]),
    ],
    targets: [
        .target(name: "CodexMenuBarKit"),
        .executableTarget(
            name: "CodexMenuBar",
            dependencies: ["CodexMenuBarKit"],
            resources: [.process("Resources")]
        ),
        .executableTarget(
            name: "CodexMenuBarSnapshot",
            dependencies: ["CodexMenuBarKit"]
        ),
        .executableTarget(
            name: "CodexProxyWidget",
            dependencies: ["CodexMenuBarKit"]
        ),
        .testTarget(
            name: "CodexMenuBarKitTests",
            dependencies: ["CodexMenuBarKit"]
        ),
    ]
)

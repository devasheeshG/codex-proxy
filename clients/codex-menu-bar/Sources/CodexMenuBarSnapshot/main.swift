import AppKit
import CodexMenuBarKit
import SwiftUI

@MainActor
func renderSnapshot(to destination: URL) throws {
    NSApplication.shared.setActivationPolicy(.prohibited)

    let model = DashboardModel()
    let rootView = PopoverView(model: model)
        .frame(width: 390, height: 620)
        .environment(\.colorScheme, .dark)

    let hostingView = NSHostingView(rootView: rootView)
    hostingView.frame = NSRect(x: 0, y: 0, width: 390, height: 620)
    hostingView.layoutSubtreeIfNeeded()

    guard let representation = hostingView.bitmapImageRepForCachingDisplay(in: hostingView.bounds) else {
        throw SnapshotError.renderingFailed
    }
    hostingView.cacheDisplay(in: hostingView.bounds, to: representation)
    guard let png = representation.representation(using: .png, properties: [:]) else {
        throw SnapshotError.encodingFailed
    }
    try png.write(to: destination)
}

enum SnapshotError: Error {
    case renderingFailed
    case encodingFailed
}

let output = CommandLine.arguments.dropFirst().first ?? "Codex-Proxy-popover.png"
Task { @MainActor in
    do {
        try renderSnapshot(to: URL(fileURLWithPath: output))
        print("Snapshot written to \(output)")
        exit(0)
    } catch {
        fputs("Snapshot failed: \(error)\n", stderr)
        exit(1)
    }
}
RunLoop.main.run()

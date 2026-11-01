import AppKit
import CodexMenuBarKit
import SwiftUI

@main
struct CodexMenuBarApp: App {
    @StateObject private var model = DashboardModel()
    var body: some Scene {
        MenuBarExtra { PopoverView(model: model) } label: {
            Image(systemName: "circle.hexagonpath").help("Codex Proxy")
        }.menuBarExtraStyle(.window)
    }
}

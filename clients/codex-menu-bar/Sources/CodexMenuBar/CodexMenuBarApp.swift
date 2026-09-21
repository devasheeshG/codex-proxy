import AppKit
import CodexMenuBarKit
import SwiftUI

@main
struct CodexMenuBarApp: App {
    @StateObject private var model = DashboardModel()

    var body: some Scene {
        MenuBarExtra {
            PopoverView(model: model)
                .frame(width: 390)
        } label: {
            Image(nsImage: OpenAIMark.image)
                .renderingMode(.template)
                .help("Codex Proxy")
        }
        .menuBarExtraStyle(.window)
    }
}

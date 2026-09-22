import AppKit
import CodexMenuBarKit
import SwiftUI

@main
struct CodexMenuBarApp: App {
    @StateObject private var model = DashboardModel()

    var body: some Scene {
        MenuBarExtra {
            PopoverView(model: model)
                .frame(
                    width: model.isConfigured ? 720 : 390,
                    height: model.isConfigured ? 640 : 360
                )
                .animation(.easeInOut(duration: 0.22), value: model.isConfigured)
        } label: {
            Image(nsImage: OpenAIMark.image)
                .renderingMode(.template)
                .help("Codex Proxy")
        }
        .menuBarExtraStyle(.window)
    }
}

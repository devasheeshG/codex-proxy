import AppKit

public enum OpenAIMark {
    public static let image: NSImage = {
        let executableDirectory = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
        let candidates = [
            Bundle.main.url(forResource: "OpenAIMark", withExtension: "svg"),
            Bundle.main.resourceURL?.appendingPathComponent("OpenAIMark.svg"),
            executableDirectory.appendingPathComponent("OpenAIMark.svg"),
        ].compactMap { $0 }

        for url in candidates {
            if let data = try? Data(contentsOf: url), let image = NSImage(data: data) {
                image.size = NSSize(width: 17, height: 17)
                image.isTemplate = true
                return image
            }
        }

        return NSImage(
            systemSymbolName: "circle.hexagonpath.fill",
            accessibilityDescription: "Codex Proxy"
        ) ?? NSImage()
    }()
}

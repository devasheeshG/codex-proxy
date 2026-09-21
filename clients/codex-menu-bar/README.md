# Codex Proxy menu bar app

Native SwiftUI menu-bar client for the Codex Proxy dashboard. It targets macOS 13 or newer and uses the same OpenAI mark, metrics, quota language, and account states as the web console.

## Build on macOS

```bash
swift build --package-path clients/codex-menu-bar --configuration release
```

The GitHub `macOS menu bar app` workflow builds an ad-hoc-signed `.app`, renders the actual SwiftUI popover into a PNG, and uploads both as workflow artifacts.

Production distribution still requires a Developer ID Application certificate and Apple notarization. Signing credentials must be stored in CI secrets and must never be committed to this repository.

# Codex Proxy menu bar app

Native SwiftUI menu-bar client for the Codex Proxy dashboard. It targets macOS 13 or newer and uses the same OpenAI mark, metrics, quota language, and account states as the web console.

## Build on macOS

```bash
swift build --package-path clients/codex-menu-bar --configuration release
```

The [Codex Proxy macOS releases](https://github.com/devasheeshG/codex-proxy/releases)
page publishes an ad-hoc-signed `Codex-Proxy-macOS.zip` together with the actual
SwiftUI overview and accounts screenshots. Unzip it and move **Codex Proxy.app**
to Applications. The app can be opened with Control-click if Gatekeeper warns
that the ad-hoc build is not notarized.

Production distribution still requires a Developer ID Application certificate and Apple notarization. Signing credentials must be stored in CI secrets and must never be committed to this repository.

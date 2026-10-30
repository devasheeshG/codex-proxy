# Codex Proxy menu bar app

Native SwiftUI menu-bar client with a WidgetKit quota widget. for the Codex Proxy dashboard. It targets macOS 13 or newer and uses the same OpenAI mark, metrics, quota language, and account states as the web console.

Open the gear button to enter the proxy base URL and the same username and
password used by the dashboard. The app validates them through the dashboard
login endpoint and stores the password and returned session token in the macOS
Keychain. No server URL or organization identity is compiled into the app.

## Build on macOS

```bash
swift build --package-path clients/codex-menu-bar --configuration release
```

The [Codex Proxy macOS releases](https://github.com/devasheeshG/codex-proxy/releases)
page publishes an ad-hoc-signed `Codex-Proxy-macOS.dmg`. Open the disk image and
drag **Codex Proxy.app** to Applications. The app can be opened with
Control-click if Gatekeeper warns that the ad-hoc build is not notarized.

Production distribution still requires a Developer ID Application certificate and Apple notarization. Signing credentials must be stored in CI secrets and must never be committed to this repository.

## Quota widget

The package includes `CodexProxyWidget`, a WidgetKit source for macOS 14+ that shows weekly and monthly usage percentages, active/total account coverage, and the next reset timestamps. The menu-bar app writes the latest authenticated quota snapshot to the App Group `group.com.recallrai.codexproxy`; the widget refreshes it every 15 minutes.

The widget target must be embedded in an Xcode app target with the same App Group entitlement before distributing a `.app`/DMG. A full Xcode host target is still required for the final signed widget extension; the Swift package target is the source/build validation layer. The Swift package build validates the widget source; the macOS release workflow packages the menu-bar app and must be extended with the widget extension when creating the signed distribution bundle.

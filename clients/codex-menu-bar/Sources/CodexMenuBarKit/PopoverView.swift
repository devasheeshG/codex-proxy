import AppKit
import SwiftUI

public struct PopoverView: View {
    @ObservedObject var model: DashboardModel
    @State private var showingSettings = false

    public init(model: DashboardModel) {
        self.model = model
        _showingSettings = State(initialValue: !model.isConfigured)
    }

    public var body: some View {
        VStack(spacing: 0) {
            header
            if showingSettings {
                ConnectionSettings(model: model) { showingSettings = false }
                    .padding(14)
            } else {
                Picker("View", selection: $model.tab) {
                    ForEach(DashboardModel.Tab.allCases, id: \.self) { tab in
                        Text(tab.rawValue).tag(tab)
                    }
                }
                .pickerStyle(.segmented)
                .tint(ProxyTheme.brand)
                .padding(.horizontal, 14)
                .padding(.top, 11)

                ScrollView {
                    Group {
                        switch model.tab {
                        case .overview: OverviewView(model: model)
                        case .accounts: AccountsView(model: model)
                        }
                    }
                    .padding(14)
                }
                .scrollIndicators(.hidden)
            }

            Divider()
            HStack {
                Text("Updated ") + Text(model.lastUpdated, style: .relative) + Text(" ago")
                Spacer()
                Button("Open dashboard") { }
                    .buttonStyle(.link)
                    .font(.caption)
                Button { NSApplication.shared.terminate(nil) } label: {
                    Label("Quit", systemImage: "power")
                }
                .buttonStyle(.borderless)
                .font(.caption)
                .foregroundStyle(ProxyTheme.muted)
                .help("Quit Codex Proxy")
            }
            .foregroundStyle(ProxyTheme.muted)
            .font(.caption2)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
        .foregroundStyle(ProxyTheme.primary)
        .background(ProxyTheme.background)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(nsImage: OpenAIMark.image)
                .renderingMode(.template)
                .foregroundStyle(ProxyTheme.primary)
                .frame(width: 28, height: 28)
                .background(ProxyTheme.panel, in: RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 2) { Text("Codex Proxy").font(.headline); Text("Menu bar dashboard").font(.caption).foregroundStyle(ProxyTheme.muted) }
            Spacer()
            Button { showingSettings.toggle() } label: { Image(systemName: showingSettings ? "xmark" : "gearshape").font(.body) }
                .buttonStyle(.borderless).help(showingSettings ? "Close settings" : "Connection settings")
            Label(model.isConfigured ? "Signed in" : "Sign in", systemImage: model.isConfigured ? "checkmark.circle" : "person.crop.circle")
                .font(.caption).foregroundStyle(model.isConfigured ? ProxyTheme.good : ProxyTheme.muted)
        }
        .padding(.horizontal, 14)
        .padding(.top, 14)
    }
}

private struct ConnectionSettings: View {
    @ObservedObject var model: DashboardModel
    @State private var baseURL: String
    @State private var username: String
    @State private var password: String
    @State private var isSigningIn = false
    @State private var errorMessage: String?
    let onClose: () -> Void

    init(model: DashboardModel, onClose: @escaping () -> Void) {
        self.model = model
        self.onClose = onClose
        _baseURL = State(initialValue: model.baseURL)
        _username = State(initialValue: model.username)
        _password = State(initialValue: model.password)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Sign in to your proxy").font(.title3.bold())
            Text("Enter the same dashboard username and password you use in the browser. Your password is stored in the macOS Keychain.").font(.caption).foregroundStyle(ProxyTheme.muted)
            TextField("Base URL", text: $baseURL).textFieldStyle(.roundedBorder)
            TextField("Username", text: $username).textFieldStyle(.roundedBorder)
            SecureField("Password", text: $password).textFieldStyle(.roundedBorder)
            if let errorMessage { Text(errorMessage).font(.caption).foregroundStyle(ProxyTheme.bad) }
            HStack {
                Spacer()
                if model.isConfigured { Button("Cancel", action: onClose).keyboardShortcut(.cancelAction) }
                Button(isSigningIn ? "Signing in…" : "Sign in") {
                    isSigningIn = true
                    errorMessage = nil
                    Task {
                        do { try await model.authenticate(baseURL: baseURL, username: username, password: password); onClose() }
                        catch { errorMessage = error.localizedDescription }
                        isSigningIn = false
                    }
                }.buttonStyle(.borderedProminent).tint(ProxyTheme.brand).keyboardShortcut(.defaultAction).disabled(isSigningIn)
            }
        }
    }
}

private struct OverviewView: View {
    @ObservedObject var model: DashboardModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Metric(title: "Tokens processed", value: "6.30B", detail: "+14.2% this month")
                Metric(title: "Requests", value: "50,706", detail: "1,842 today")
            }
            HStack(spacing: 8) {
                Metric(title: "This month", value: "$1,626.80", detail: "$4,580.97 all-time")
                Metric(title: "Cache hit rate", value: "96.8%", detail: "5.91B cache-read")
            }
            SectionTitle(title: "Pool windows", action: "Accounts")
            HStack(spacing: 8) {
                Quota(title: "5-hour average", value: 46, reset: "rolling")
                Quota(title: "Weekly average", value: 54, reset: "rolling")
            }
            SectionTitle(title: "Activity · 30 days", action: "Events")
            ActivitySparkline()
            HStack(spacing: 8) {
                Button { model.tab = .overview } label: { Label("Open Overview", systemImage: "arrow.up.right.square") }
                    .buttonStyle(.borderedProminent).tint(ProxyTheme.brand)
                Button { model.tab = .accounts } label: { Label("Open Accounts", systemImage: "person.2") }
                    .buttonStyle(.bordered)
            }
        }
    }
}

private struct AccountsView: View {
    @ObservedObject var model: DashboardModel
    @State private var filter = "All"

    private var visibleAccounts: [DashboardModel.Account] {
        model.accounts.filter {
            switch filter {
            case "Authenticated": return $0.authenticated
            case "Usable": return $0.usable
            default: return true
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 2) { Text("Account pool").font(.title3.bold()); Text("12 accounts · 8 usable · 2 re-auth · 2 cooling").font(.caption).foregroundStyle(ProxyTheme.muted) }
                Spacer()
            }
            Picker("Filter", selection: $filter) { ForEach(["All", "Authenticated", "Usable"], id: \.self) { Text($0).tag($0) } }
                .pickerStyle(.segmented)
                .controlSize(.small)
            ForEach(visibleAccounts) { account in AccountRow(account: account) }
            Button { model.refresh() } label: { Label(model.isRefreshing ? "Refreshing…" : "Refresh limits", systemImage: "arrow.clockwise") }
                .buttonStyle(.borderedProminent).tint(ProxyTheme.brand)
                .disabled(model.isRefreshing)
        }
    }
}

private struct AccountRow: View {
    let account: DashboardModel.Account
    var stateColor: Color { switch account.state { case .ready: ProxyTheme.good; case .cooling: ProxyTheme.warn; case .reauth: ProxyTheme.bad } }
    var stateLabel: String { switch account.state { case .ready: "Ready"; case .cooling: "Cooling"; case .reauth: "Re-auth" } }
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack { VStack(alignment: .leading, spacing: 2) { Text(account.name).font(.subheadline.bold()); Text(account.email).font(.caption2).foregroundStyle(ProxyTheme.muted) }; Spacer(); Label(stateLabel, systemImage: "circle.fill").font(.caption2).foregroundStyle(stateColor) }
            HStack(spacing: 8) { Quota(title: "5-hour", value: account.fiveHour, reset: "resets " + account.fiveHourReset); Quota(title: "Weekly", value: account.weekly, reset: "resets " + account.weeklyReset) }
        }
        .padding(10)
        .background(ProxyTheme.card, in: RoundedRectangle(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(ProxyTheme.border, lineWidth: 1))
    }
}

private struct Metric: View { let title: String; let value: String; let detail: String; var body: some View { VStack(alignment: .leading, spacing: 5) { Text(title.uppercased()).font(.caption2).foregroundStyle(ProxyTheme.muted); Text(value).font(.system(.headline, design: .monospaced).weight(.semibold)); Text(detail).font(.caption2).foregroundStyle(ProxyTheme.muted) }.frame(maxWidth: .infinity, alignment: .leading).padding(10).background(ProxyTheme.card, in: RoundedRectangle(cornerRadius: 9)) } }
private struct Quota: View { let title: String; let value: Int; let reset: String; var color: Color { value >= 95 ? ProxyTheme.bad : value >= 80 ? ProxyTheme.warn : ProxyTheme.good }; var body: some View { VStack(alignment: .leading, spacing: 5) { HStack { Text(title).font(.caption2); Spacer(); Text("\(value)%").font(.caption2.monospaced()).bold() }; ProgressView(value: Double(value), total: 100).tint(color); Text("\(reset)").font(.caption2).foregroundStyle(ProxyTheme.muted) }.frame(maxWidth: .infinity, alignment: .leading).padding(9).background(ProxyTheme.panel, in: RoundedRectangle(cornerRadius: 8)) } }
private struct SectionTitle: View { let title: String; let action: String; var body: some View { HStack { Text(title.uppercased()).font(.caption2).foregroundStyle(ProxyTheme.muted); Spacer(); Text(action).font(.caption2).foregroundStyle(ProxyTheme.brand) } } }
private struct ActivitySparkline: View { var body: some View { GeometryReader { geo in Path { path in let points: [CGFloat] = [0.80,0.72,0.76,0.56,0.63,0.44,0.50,0.30,0.36,0.19,0.27,0.14,0.21,0.06]; for (index, point) in points.enumerated() { let x = geo.size.width * CGFloat(index) / CGFloat(points.count - 1); let y = geo.size.height * point; index == 0 ? path.move(to: CGPoint(x: x, y: y)) : path.addLine(to: CGPoint(x: x, y: y)) } }.stroke(ProxyTheme.brand, lineWidth: 2).shadow(color: ProxyTheme.brand.opacity(0.35), radius: 4) }.frame(height: 52).padding(9).background(ProxyTheme.brand.opacity(0.08), in: RoundedRectangle(cornerRadius: 9)) } }

private enum ProxyTheme {
    static let background = Color(red: 13/255, green: 13/255, blue: 13/255)
    static let panel = Color(red: 21/255, green: 21/255, blue: 21/255)
    static let card = Color(red: 28/255, green: 28/255, blue: 28/255)
    static let border = Color(red: 48/255, green: 48/255, blue: 48/255)
    static let primary = Color(red: 247/255, green: 247/255, blue: 248/255)
    static let muted = Color(red: 152/255, green: 152/255, blue: 163/255)
    static let brand = Color(red: 16/255, green: 163/255, blue: 127/255)
    static let good = Color(red: 54/255, green: 201/255, blue: 154/255)
    static let warn = Color(red: 227/255, green: 163/255, blue: 61/255)
    static let bad = Color(red: 240/255, green: 123/255, blue: 114/255)
}

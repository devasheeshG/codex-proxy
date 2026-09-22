import AppKit
import SwiftUI

public struct PopoverView: View {
    @ObservedObject var model: DashboardModel
    @State private var showingSettings = false

    public init(model: DashboardModel) {
        self.model = model
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
                .foregroundStyle(.secondary)
                .help("Quit Codex Proxy")
            }
            .foregroundStyle(.secondary)
            .font(.caption2)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
        .background(.regularMaterial)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(nsImage: OpenAIMark.image)
                .renderingMode(.template)
                .foregroundStyle(.primary)
                .frame(width: 28, height: 28)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 2) { Text("Codex Proxy").font(.headline); Text("Menu bar dashboard").font(.caption).foregroundStyle(.secondary) }
            Spacer()
            Button { showingSettings.toggle() } label: { Image(systemName: showingSettings ? "xmark" : "gearshape").font(.body) }
                .buttonStyle(.borderless).help(showingSettings ? "Close settings" : "Connection settings")
            Label(model.isConfigured ? "Configured" : "Not configured", systemImage: model.isConfigured ? "checkmark.circle" : "circle.dashed")
                .font(.caption).foregroundStyle(model.isConfigured ? .green : .secondary)
        }
        .padding(.horizontal, 14)
        .padding(.top, 14)
    }
}

private struct ConnectionSettings: View {
    @ObservedObject var model: DashboardModel
    @State private var baseURL: String
    @State private var username: String
    @State private var secret: String
    let onClose: () -> Void

    init(model: DashboardModel, onClose: @escaping () -> Void) {
        self.model = model
        self.onClose = onClose
        _baseURL = State(initialValue: model.baseURL)
        _username = State(initialValue: model.username)
        _secret = State(initialValue: model.secret)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Connection").font(.title3.bold())
            Text("Connect this menu-bar app to any compatible proxy. Values are stored locally; the secret is kept in the macOS Keychain.").font(.caption).foregroundStyle(.secondary)
            TextField("Base URL", text: $baseURL).textFieldStyle(.roundedBorder)
            TextField("Username (optional)", text: $username).textFieldStyle(.roundedBorder)
            SecureField("API key or password", text: $secret).textFieldStyle(.roundedBorder)
            HStack {
                Spacer()
                Button("Cancel", action: onClose).keyboardShortcut(.cancelAction)
                Button("Save") {
                    model.baseURL = baseURL
                    model.username = username
                    model.secret = secret
                    model.saveConnection()
                    onClose()
                }.buttonStyle(.borderedProminent).tint(.green).keyboardShortcut(.defaultAction)
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
                    .buttonStyle(.borderedProminent).tint(.green)
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
                VStack(alignment: .leading, spacing: 2) { Text("Account pool").font(.title3.bold()); Text("12 accounts · 8 usable · 2 re-auth · 2 cooling").font(.caption).foregroundStyle(.secondary) }
                Spacer()
            }
            Picker("Filter", selection: $filter) { ForEach(["All", "Authenticated", "Usable"], id: \.self) { Text($0).tag($0) } }
                .pickerStyle(.segmented)
                .controlSize(.small)
            ForEach(visibleAccounts) { account in AccountRow(account: account) }
            Button { model.refresh() } label: { Label(model.isRefreshing ? "Refreshing…" : "Refresh limits", systemImage: "arrow.clockwise") }
                .buttonStyle(.borderedProminent).tint(.green)
                .disabled(model.isRefreshing)
        }
    }
}

private struct AccountRow: View {
    let account: DashboardModel.Account
    var stateColor: Color { switch account.state { case .ready: .green; case .cooling: .orange; case .reauth: .red } }
    var stateLabel: String { switch account.state { case .ready: "Ready"; case .cooling: "Cooling"; case .reauth: "Re-auth" } }
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack { VStack(alignment: .leading, spacing: 2) { Text(account.name).font(.subheadline.bold()); Text(account.email).font(.caption2).foregroundStyle(.secondary) }; Spacer(); Label(stateLabel, systemImage: "circle.fill").font(.caption2).foregroundStyle(stateColor) }
            HStack(spacing: 8) { Quota(title: "5-hour", value: account.fiveHour, reset: "resets " + account.fiveHourReset); Quota(title: "Weekly", value: account.weekly, reset: "resets " + account.weeklyReset) }
        }
        .padding(10)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(.quaternary, lineWidth: 1))
    }
}

private struct Metric: View { let title: String; let value: String; let detail: String; var body: some View { VStack(alignment: .leading, spacing: 5) { Text(title.uppercased()).font(.caption2).foregroundStyle(.secondary); Text(value).font(.system(.headline, design: .monospaced).weight(.semibold)); Text(detail).font(.caption2).foregroundStyle(.secondary) }.frame(maxWidth: .infinity, alignment: .leading).padding(10).background(.quaternary.opacity(0.32), in: RoundedRectangle(cornerRadius: 9)) } }
private struct Quota: View { let title: String; let value: Int; let reset: String; var color: Color { value >= 95 ? .red : value >= 80 ? .orange : .green }; var body: some View { VStack(alignment: .leading, spacing: 5) { HStack { Text(title).font(.caption2); Spacer(); Text("\(value)%").font(.caption2.monospaced()).bold() }; ProgressView(value: Double(value), total: 100).tint(color); Text("\(reset)").font(.caption2).foregroundStyle(.secondary) }.frame(maxWidth: .infinity, alignment: .leading).padding(9).background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 8)) } }
private struct SectionTitle: View { let title: String; let action: String; var body: some View { HStack { Text(title.uppercased()).font(.caption2).foregroundStyle(.secondary); Spacer(); Text(action).font(.caption2).foregroundStyle(.green) } } }
private struct ActivitySparkline: View { var body: some View { GeometryReader { geo in Path { path in let points: [CGFloat] = [0.80,0.72,0.76,0.56,0.63,0.44,0.50,0.30,0.36,0.19,0.27,0.14,0.21,0.06]; for (index, point) in points.enumerated() { let x = geo.size.width * CGFloat(index) / CGFloat(points.count - 1); let y = geo.size.height * point; index == 0 ? path.move(to: CGPoint(x: x, y: y)) : path.addLine(to: CGPoint(x: x, y: y)) } }.stroke(.green, lineWidth: 2).shadow(color: .green.opacity(0.35), radius: 4) }.frame(height: 52).padding(9).background(.green.opacity(0.08), in: RoundedRectangle(cornerRadius: 9)) } }

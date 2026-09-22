import Combine
import Foundation
import Security

@MainActor
public final class DashboardModel: ObservableObject {
    enum Tab: String, CaseIterable, Hashable { case overview = "Overview", accounts = "Accounts" }
    enum AccountState: Equatable { case ready, cooling, reauth }
    struct Account: Identifiable {
        let id = UUID()
        let name: String
        let email: String
        let state: AccountState
        /// Mirrors the dashboard's Authenticated filter: credentials are present
        /// (including accounts that are currently cooling down).
        let authenticated: Bool
        /// Mirrors the dashboard's Usable filter: the router can select this
        /// account for a request right now.
        let usable: Bool
        let fiveHour: Int
        let weekly: Int
        let fiveHourReset: String
        let weeklyReset: String
    }

    @Published var tab: Tab = .overview
    @Published var lastUpdated = Date()
    @Published var isRefreshing = false
    @Published var selectedProvider = "Codex pool"
    @Published var baseURL: String
    @Published var username: String
    @Published var secret: String

    public init() {
        baseURL = UserDefaults.standard.string(forKey: "proxy.baseURL") ?? ""
        username = UserDefaults.standard.string(forKey: "proxy.username") ?? ""
        secret = SecureStore.read(service: "proxy.credentials") ?? ""
    }

    var isConfigured: Bool {
        !baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            (!secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
                !username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    func saveConnection() {
        baseURL = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        username = username.trimmingCharacters(in: .whitespacesAndNewlines)
        UserDefaults.standard.set(baseURL, forKey: "proxy.baseURL")
        UserDefaults.standard.set(username, forKey: "proxy.username")
        SecureStore.write(secret, service: "proxy.credentials")
    }

    public func showAccounts() {
        tab = .accounts
    }

    let accounts: [Account] = [
        Account(name: "Account 01", email: "account01@example.test", state: .ready, authenticated: true, usable: true, fiveHour: 18, weekly: 42, fiveHourReset: "3h 34m", weeklyReset: "6d 22h"),
        Account(name: "Account 02", email: "account02@example.test", state: .ready, authenticated: true, usable: true, fiveHour: 63, weekly: 71, fiveHourReset: "2h 12m", weeklyReset: "4d 17h"),
        Account(name: "Account 03", email: "account03@example.test", state: .cooling, authenticated: true, usable: false, fiveHour: 100, weekly: 45, fiveHourReset: "1h 56m", weeklyReset: "2d 17h"),
        Account(name: "Account 04", email: "account04@example.test", state: .reauth, authenticated: false, usable: false, fiveHour: 0, weekly: 2, fiveHourReset: "unknown", weeklyReset: "5d 23h"),
    ]

    func refresh() {
        isRefreshing = true
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(500))
            lastUpdated = Date()
            isRefreshing = false
        }
    }
}

private enum SecureStore {
    static func read(service: String) -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func write(_ value: String, service: String) {
        let data = Data(value.utf8)
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service]
        SecItemDelete(query as CFDictionary)
        let item = query.merging([kSecValueData as String: data]) { _, new in new }
        SecItemAdd(item as CFDictionary, nil)
    }
}

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
    @Published var password: String
    @Published private(set) var isAuthenticated = false
    @Published private(set) var overview: ProxyOverview?
    @Published private(set) var liveAccounts: [ProxyAccount] = []
    @Published private(set) var loadError: String?

    public init() {
        baseURL = UserDefaults.standard.string(forKey: "proxy.baseURL") ?? ""
        username = UserDefaults.standard.string(forKey: "proxy.username") ?? ""
        password = SecureStore.read(service: "proxy.dashboard.password") ?? ""
        isAuthenticated = SecureStore.read(service: "proxy.dashboard.token") != nil
    }

    public var isConfigured: Bool {
        isAuthenticated
    }

    func authenticate(baseURL rawBaseURL: String, username rawUsername: String, password rawPassword: String) async throws {
        let baseURL = rawBaseURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let username = rawUsername.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !baseURL.isEmpty, !username.isEmpty, !rawPassword.isEmpty else { throw ConnectionError.missingFields }
        let loginPath = baseURL.hasSuffix("/api") ? "/v1/auth/login" : "/api/v1/auth/login"
        guard let url = URL(string: baseURL + loginPath) else { throw ConnectionError.invalidURL }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(LoginRequest(username: username, password: rawPassword))
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw ConnectionError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONDecoder().decode(ErrorResponse.self, from: data).detail) ?? "Sign-in failed (HTTP \(http.statusCode))."
            throw ConnectionError.server(detail)
        }
        let token = try JSONDecoder().decode(LoginResponse.self, from: data).token
        self.baseURL = baseURL
        self.username = username
        self.password = rawPassword
        UserDefaults.standard.set(baseURL, forKey: "proxy.baseURL")
        UserDefaults.standard.set(username, forKey: "proxy.username")
        SecureStore.write(rawPassword, service: "proxy.dashboard.password")
        SecureStore.write(token, service: "proxy.dashboard.token")
        isAuthenticated = true
        await refresh()
    }

    public func showAccounts() {
        tab = .accounts
    }

    var accounts: [Account] {
        liveAccounts.map { account in
            let state: AccountState = account.providerHealth == "REAUTH_REQUIRED" ? .reauth : (account.status == "COOLDOWN" ? .cooling : .ready)
            return Account(name: account.label, email: account.accountEmail ?? "—", state: state,
                           authenticated: account.providerHealth != "REAUTH_REQUIRED", usable: account.providerHealth == "HEALTHY" && account.status == "ACTIVE",
                           fiveHour: Int((account.fiveHourUsedPct ?? 0) * 100), weekly: Int((account.weeklyUsedPct ?? 0) * 100),
                           fiveHourReset: formatReset(account.fiveHourResetAt), weeklyReset: formatReset(account.weeklyResetAt))
        }
    }

    private func formatReset(_ date: Date?) -> String { guard let date else { return "unknown" }; return RelativeDateTimeFormatter().localizedString(for: date, relativeTo: Date()) }

    func refresh() {
        guard isAuthenticated else { return }
        isRefreshing = true; loadError = nil
        Task { @MainActor in
            defer { isRefreshing = false }
            do {
                let base = baseURL.hasSuffix("/api") ? String(baseURL.dropLast(4)) : baseURL
                guard let url = URL(string: base.hasSuffix("/") ? base : base + "/") else { throw ProxyAPIError.notConfigured }
                let api = ProxyAPI(baseURL: url, token: SecureStore.read(service: "proxy.dashboard.token") ?? "")
                async let nextOverview = api.overview(); async let nextAccounts = api.accounts()
                overview = try await nextOverview; liveAccounts = try await nextAccounts; lastUpdated = Date()
                WidgetQuotaCache.write(overview: overview!, activeAccounts: liveAccounts.filter { $0.providerHealth != "REAUTH_REQUIRED" }.count, totalAccounts: liveAccounts.count)
            } catch { loadError = error.localizedDescription }
        }
    }
}

private struct LoginRequest: Encodable { let username: String; let password: String }
private struct LoginResponse: Decodable { let token: String }
private struct ErrorResponse: Decodable { let detail: String }
private enum ConnectionError: LocalizedError {
    case missingFields, invalidURL, invalidResponse, server(String)
    var errorDescription: String? {
        switch self {
        case .missingFields: "Base URL, username, and password are required."
        case .invalidURL: "Enter a valid proxy base URL."
        case .invalidResponse: "The proxy returned an invalid response."
        case .server(let message): message
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

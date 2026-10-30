import Foundation

struct ProxyOverview: Decodable {
    let activeAccounts: Int
    let totalAccounts: Int
    let fiveHourAveragePct: Double
    let weeklyAveragePct: Double
    let monthlyAveragePct: Double
    let fiveHourResetAt: Date?
    let weeklyResetAt: Date?
    let monthlyResetAt: Date?
    let requests: Int
    let tokens: Int

    enum CodingKeys: String, CodingKey {
        case activeAccounts = "active_accounts", totalAccounts = "total_accounts"
        case fiveHourAveragePct = "five_hour_average_pct", weeklyAveragePct = "weekly_average_pct"
        case monthlyAveragePct = "monthly_average_pct", fiveHourResetAt = "five_hour_reset_at"
        case weeklyResetAt = "weekly_reset_at", monthlyResetAt = "monthly_reset_at"
        case requests, tokens
    }
}

struct ProxyAccount: Decodable, Identifiable {
    let id: UUID
    let label: String
    let accountEmail: String?
    let status: String
    let providerHealth: String
    let fiveHourUsedPct: Double?
    let weeklyUsedPct: Double?
    let fiveHourResetAt: Date?
    let weeklyResetAt: Date?
    enum CodingKeys: String, CodingKey {
        case id, label, accountEmail = "account_email", status
        case providerHealth = "provider_health", fiveHourUsedPct = "five_hour_used_pct"
        case weeklyUsedPct = "weekly_used_pct", fiveHourResetAt = "five_hour_reset_at"
        case weeklyResetAt = "weekly_reset_at"
    }
}

struct ProxyAccountsResponse: Decodable { let accounts: [ProxyAccount] }

enum ProxyAPIError: LocalizedError {
    case notConfigured, invalidResponse(Int), server(String)
    var errorDescription: String? {
        switch self {
        case .notConfigured: "Sign in to load live proxy data."
        case .invalidResponse(let status): "Proxy request failed (HTTP \(status))."
        case .server(let message): message
        }
    }
}

struct ProxyAPI {
    let baseURL: URL
    let token: String
    private var decoder: JSONDecoder {
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601; return d
    }
    private func get<T: Decodable>(_ path: String) async throws -> T {
        let url = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: url); request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw ProxyAPIError.invalidResponse(0) }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? decoder.decode([String: String].self, from: data))["detail"] ?? "Proxy request failed."
            throw ProxyAPIError.server(detail)
        }
        return try decoder.decode(T.self, from: data)
    }
    func overview() async throws -> ProxyOverview { try await get("api/v1/stats/overview") }
    func accounts() async throws -> [ProxyAccount] { try await get("api/v1/accounts") }
}

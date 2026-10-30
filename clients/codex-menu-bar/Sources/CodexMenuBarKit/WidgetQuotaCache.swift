import Foundation

public struct WidgetQuotaSnapshot: Codable, Sendable {
    public let updatedAt: Date
    public let weekly: Double
    public let monthly: Double
    public let activeAccounts: Int
    public let totalAccounts: Int
    public let weeklyReset: Date?
    public let monthlyReset: Date?
}

public enum WidgetQuotaCache {
    static let suite = "group.com.recallrai.codexproxy"
    static let key = "quota-widget-snapshot"
    static let defaults = UserDefaults(suiteName: suite)
    static func write(overview: ProxyOverview, activeAccounts: Int, totalAccounts: Int) {
        let snapshot = WidgetQuotaSnapshot(updatedAt: Date(), weekly: overview.weeklyAveragePct, monthly: overview.monthlyAveragePct, activeAccounts: activeAccounts, totalAccounts: totalAccounts, weeklyReset: overview.weeklyResetAt, monthlyReset: overview.monthlyResetAt)
        if let data = try? JSONEncoder().encode(snapshot) { defaults?.set(data, forKey: key) }
    }
    public static func read() -> WidgetQuotaSnapshot? {
        guard let data = defaults?.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(WidgetQuotaSnapshot.self, from: data)
    }
}

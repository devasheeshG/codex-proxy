import Combine
import Foundation

@MainActor
public final class DashboardModel: ObservableObject {
    enum Tab: String, CaseIterable, Hashable { case overview = "Overview", accounts = "Accounts" }
    enum AccountState: Equatable { case ready, cooling, reauth }
    struct Account: Identifiable {
        let id = UUID()
        let name: String
        let email: String
        let state: AccountState
        let fiveHour: Int
        let weekly: Int
        let fiveHourReset: String
        let weeklyReset: String
    }

    @Published var tab: Tab = .overview
    @Published var lastUpdated = Date()
    @Published var isRefreshing = false
    @Published var selectedProvider = "Codex pool"

    public init() {}

    let accounts: [Account] = [
        Account(name: "Shabbir", email: "jamilakhand.jk@gmail.com", state: .ready, fiveHour: 18, weekly: 42, fiveHourReset: "3h 34m", weeklyReset: "6d 22h"),
        Account(name: "Devasheesh", email: "devasheesh@recallrai.com", state: .ready, fiveHour: 63, weekly: 71, fiveHourReset: "2h 12m", weeklyReset: "4d 17h"),
        Account(name: "Animesh", email: "animesh3720@gmail.com", state: .cooling, fiveHour: 100, weekly: 45, fiveHourReset: "1h 56m", weeklyReset: "2d 17h"),
        Account(name: "Vishal", email: "vishalpachpor06@gmail.com", state: .reauth, fiveHour: 0, weekly: 2, fiveHourReset: "unknown", weeklyReset: "5d 23h"),
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

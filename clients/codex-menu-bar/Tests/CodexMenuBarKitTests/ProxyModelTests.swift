import XCTest
@testable import CodexMenuBarKit

final class ProxyModelTests: XCTestCase {
    func testWidgetSnapshotRoundTrips() throws {
        let snapshot = WidgetQuotaSnapshot(updatedAt: Date(timeIntervalSince1970: 123), weekly: 42, monthly: 17, activeAccounts: 8, totalAccounts: 12, weeklyReset: Date(timeIntervalSince1970: 456), monthlyReset: nil)
        let data = try JSONEncoder().encode(snapshot)
        let decoded = try JSONDecoder().decode(WidgetQuotaSnapshot.self, from: data)
        XCTAssertEqual(decoded.weekly, 42)
        XCTAssertEqual(decoded.monthly, 17)
        XCTAssertEqual(decoded.activeAccounts, 8)
        XCTAssertEqual(decoded.totalAccounts, 12)
        XCTAssertEqual(decoded.weeklyReset, Date(timeIntervalSince1970: 456))
    }

    func testOverviewUsesSnakeCaseAPIKeys() throws {
        let data = Data("{\"active_accounts\":8,\"total_accounts\":12,\"five_hour_average_pct\":31.5,\"weekly_average_pct\":42.5,\"monthly_average_pct\":17.0,\"five_hour_reset_at\":null,\"weekly_reset_at\":null,\"monthly_reset_at\":null,\"requests\":10,\"tokens\":100}".utf8)
        let decoder = JSONDecoder()
        let overview = try decoder.decode(ProxyOverview.self, from: data)
        XCTAssertEqual(overview.activeAccounts, 8)
        XCTAssertEqual(overview.weeklyAveragePct, 42.5)
        XCTAssertEqual(overview.monthlyAveragePct, 17)
    }
}

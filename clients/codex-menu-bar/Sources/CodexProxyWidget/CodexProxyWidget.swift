import SwiftUI
import WidgetKit

struct QuotaWidgetEntry: TimelineEntry {
    let date: Date
    let weekly: Double
    let monthly: Double
    let active: Int
    let total: Int
    let weeklyReset: Date?
    let monthlyReset: Date?
    let error: String?
}

struct QuotaWidgetProvider: TimelineProvider {
    func placeholder(in context: Context) -> QuotaWidgetEntry { QuotaWidgetEntry(date: Date(), weekly: 54, monthly: 38, active: 8, total: 12, weeklyReset: nil, monthlyReset: nil, error: nil) }
    func getSnapshot(in context: Context, completion: @escaping (QuotaWidgetEntry) -> Void) { completion(placeholder(in: context)) }
    func getTimeline(in context: Context, completion: @escaping (Timeline<QuotaWidgetEntry>) -> Void) {
        let snapshot = WidgetQuotaCache.read()
        let entry = snapshot.map { QuotaWidgetEntry(date: $0.updatedAt, weekly: $0.weekly, monthly: $0.monthly, active: $0.activeAccounts, total: $0.totalAccounts, weeklyReset: $0.weeklyReset, monthlyReset: $0.monthlyReset, error: nil) } ?? QuotaWidgetEntry(date: Date(), weekly: 0, monthly: 0, active: 0, total: 0, weeklyReset: nil, monthlyReset: nil, error: "Open Codex Proxy to load quota")
        completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(900))))
    }
}

struct QuotaWidgetView: View {
    let entry: QuotaWidgetEntry
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Codex Proxy").font(.headline)
            if let error = entry.error { Text(error).font(.caption).foregroundStyle(.secondary) }
            else {
                HStack { metric("Weekly", entry.weekly); metric("Monthly", entry.monthly) }
                Text("\\(entry.active)/\\(entry.total) accounts active").font(.caption).foregroundStyle(.secondary)
                if let reset = entry.weeklyReset { Text("Weekly reset \\(reset, style: .relative)").font(.caption2).foregroundStyle(.secondary) }
                if let reset = entry.monthlyReset { Text("Monthly reset \\(reset, style: .relative)").font(.caption2).foregroundStyle(.secondary) }
            }
        }.containerBackground(.background, for: .widget)
    }
    private func metric(_ label: String, _ value: Double) -> some View { VStack(alignment: .leading) { Text("\\(Int(value))%").font(.title2.bold()).monospacedDigit(); Text(label).font(.caption).foregroundStyle(.secondary) } }
}

@main
struct CodexProxyWidget: Widget {
    let kind = "CodexProxyQuotaWidget"
    var body: some WidgetConfiguration { StaticConfiguration(kind: kind, provider: QuotaWidgetProvider()) { QuotaWidgetView(entry: $0) }.configurationDisplayName("Codex quota").description("Weekly and monthly proxy usage, account coverage, and reset times.").supportedFamilies([.systemSmall, .systemMedium]) }
}

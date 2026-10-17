import SwiftUI
import KeyFlowCore

/// List of the rows an import couldn't use, grouped by reason. Tapping a row shows its full raw
/// data (the list itself truncates long rows); "Export skipped" hands the caller a CSV in the
/// same columns the importer expects, plus a reason column, via the same save flow the regular
/// export uses.
struct SkippedRowsView: View {
    let skipped: [SkippedRow]
    let onExport: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var detailRow: SkippedRow?

    private var grouped: [(reason: String, rows: [SkippedRow])] {
        let dict = Dictionary(grouping: skipped, by: \.reason)
        return dict.keys.sorted().map { ($0, dict[$0] ?? []) }
    }

    var body: some View {
        VStack(spacing: 0) {
            List {
                ForEach(grouped, id: \.reason) { group in
                    Section("\(group.reason) (\(group.rows.count))") {
                        ForEach(group.rows) { row in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(row.trigger.isEmpty ? "(no trigger)" : row.trigger).bold()
                                Text("Row \(row.rowNumber) · \(row.rawRow)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            .contentShape(Rectangle())
                            .onTapGesture { detailRow = row }
                        }
                    }
                }
            }
        }
        .frame(minWidth: 480, minHeight: 420)
        .navigationTitle("Skipped rows (\(skipped.count))")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Close") { dismiss() }
            }
            ToolbarItem(placement: .primaryAction) {
                Button(action: onExport) {
                    Label("Export skipped", systemImage: "square.and.arrow.up")
                }
            }
        }
        .sheet(item: $detailRow) { row in
            VStack(alignment: .leading, spacing: 12) {
                Text(row.reason).font(.headline)
                ScrollView {
                    Text(row.rawRow)
                        .font(.system(.body, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                HStack {
                    Spacer()
                    Button("Close") { detailRow = nil }
                }
            }
            .padding(20)
            .frame(width: 420, height: 260)
        }
    }
}

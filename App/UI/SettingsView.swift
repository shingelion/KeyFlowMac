import SwiftUI
import UniformTypeIdentifiers
import KeyFlowCore

/// Result of the last completed import, shown as a summary alert with a "View skipped" drill-in.
private struct ImportSummary {
    let importedCount: Int
    let skipped: [SkippedRow]
}

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState
    @State private var statusMessage: String? = nil
    @State private var overwriteDuplicatesOnImport = false
    @State private var importSummary: ImportSummary? = nil
    @State private var isShowingSkippedRows = false
    @State private var isShowingSyncExplainer = false

    var body: some View {
        Form {
            Section("General") {
                Toggle("Show in Dock", isOn: $appState.settingsStore.showInDock)
                Text("Off keeps KeyFlow out of the Dock and Cmd+Tab — the menu bar icon works the same either way.")
                    .font(.caption).foregroundStyle(.secondary)
            }

            Section("Accessibility") {
                if !appState.isAccessibilityTrusted {
                    AccessibilityStatusView(onEnableClick: AccessibilityPermission.openSystemSettings)
                } else {
                    Toggle("Expansion active", isOn: $appState.settingsStore.serviceEnabled)
                    Text("Pause to temporarily stop all expansion without revoking the permission.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            Section("Matching") {
                Toggle("Case sensitive", isOn: $appState.settingsStore.caseSensitive)
                Toggle("Preserve typed case", isOn: $appState.settingsStore.preserveCase)
                    .disabled(appState.settingsStore.caseSensitive)
                Text("If you type ;ADDR in caps, the expansion is capitalized too.")
                    .font(.caption).foregroundStyle(.secondary)

                Picker("Expand on", selection: $appState.settingsStore.expansionKey) {
                    Text("Immediate match").tag(ExpansionKey.immediate)
                    Text("Space / Tab / Return").tag(ExpansionKey.boundaryKey)
                }
                .pickerStyle(.segmented)

                TextField("Required prefix character (optional, e.g. ;)", text: Binding(
                    get: { appState.settingsStore.triggerPrefix },
                    set: { appState.settingsStore.triggerPrefix = String($0.prefix(1)) }
                ))
            }

            Section("Sync") {
                Toggle("Sync via Syncthing", isOn: Binding(
                    get: { appState.syncEngine.isEnabled },
                    set: { newValue in
                        if newValue && !appState.syncEngine.hasShownExplainer {
                            isShowingSyncExplainer = true
                        }
                        appState.syncEngine.isEnabled = newValue
                    }
                ))

                HStack {
                    Text(appState.syncEngine.folderPath ?? "No folder selected")
                        .font(.caption)
                        .foregroundStyle(appState.syncEngine.folderPath == nil ? .secondary : .primary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Button(appState.syncEngine.folderPath == nil ? "Choose Folder…" : "Change…") {
                        appState.syncEngine.selectFolder()
                    }
                }

                HStack {
                    Button("Sync Now") { appState.syncEngine.syncNow() }
                        .disabled(!appState.syncEngine.isEnabled || !appState.syncEngine.isConfigured)
                    Spacer()
                    Text(lastSyncedLabel)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                if let error = appState.syncEngine.lastError {
                    Text(error).font(.caption).foregroundStyle(.red)
                }

                Button("About Syncthing sync…") { isShowingSyncExplainer = true }
                    .font(.caption)
                    .buttonStyle(.plain)
                    .foregroundStyle(.blue)
            }

            Section("Import & Export") {
                HStack {
                    Button("Export JSON") {
                        exportFile(utType: .json, suggestedName: "keyflow_export.json") {
                            JSONImporter.toJSON(appState.shortcutStore.shortcuts, folderNamesById: folderNamesById())
                        }
                    }
                    Button("Export CSV") {
                        exportFile(utType: .commaSeparatedText, suggestedName: "keyflow_export.csv") {
                            CSVImporter.toCSV(appState.shortcutStore.shortcuts, folderNamesById: folderNamesById())
                        }
                    }
                }

                Toggle("Overwrite existing shortcuts with matching triggers", isOn: $overwriteDuplicatesOnImport)
                    .font(.caption)

                Button("Import JSON or CSV…", action: importFile)

                if let statusMessage {
                    Text(statusMessage).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .padding(20)
        .frame(width: 480)
        .onAppear { appState.refreshAccessibilityStatus() }
        .alert(
            importSummary.map { "\($0.importedCount) imported" + ($0.skipped.isEmpty ? "." : ", \($0.skipped.count) skipped.") } ?? "",
            isPresented: Binding(get: { importSummary != nil }, set: { if !$0 { importSummary = nil } })
        ) {
            if let importSummary, !importSummary.skipped.isEmpty {
                Button("View skipped") { isShowingSkippedRows = true }
                Button("OK", role: .cancel) {}
            } else {
                Button("OK", role: .cancel) {}
            }
        }
        .sheet(isPresented: $isShowingSkippedRows) {
            NavigationStack {
                SkippedRowsView(skipped: importSummary?.skipped ?? [], onExport: exportSkippedRows)
            }
        }
        .sheet(isPresented: $isShowingSyncExplainer) {
            SyncExplainerView {
                appState.syncEngine.hasShownExplainer = true
                isShowingSyncExplainer = false
            }
        }
    }

    private var lastSyncedLabel: String {
        guard let lastSyncedAt = appState.syncEngine.lastSyncedAt else { return "Never synced" }
        return "Last synced \(lastSyncedAt.formatted(.relative(presentation: .named)))"
    }

    private func folderNamesById() -> [UUID: String] {
        Dictionary(uniqueKeysWithValues: appState.folderStore.folders.map { ($0.id, $0.name) })
    }

    private func exportFile(utType: UTType, suggestedName: String, content: () -> String) {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [utType]
        panel.nameFieldStringValue = suggestedName
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try content().write(to: url, atomically: true, encoding: .utf8)
            statusMessage = "Exported to \(url.lastPathComponent)."
        } catch {
            statusMessage = "Export failed: \(error.localizedDescription)"
        }
    }

    private func exportSkippedRows() {
        guard let skipped = importSummary?.skipped, !skipped.isEmpty else { return }
        exportFile(utType: .commaSeparatedText, suggestedName: "keyflow_skipped_rows.csv") {
            CSVImporter.skippedToCSV(skipped)
        }
    }

    private func importFile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.json, .commaSeparatedText, .plainText]
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let text = try String(contentsOf: url, encoding: .utf8)
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            let result = (trimmed.hasPrefix("{") || trimmed.hasPrefix("["))
                ? JSONImporter.parse(text)
                : CSVImporter.parse(text)

            Task {
                // Folder-name -> id is cached per name since a bulk import often repeats the
                // same handful of folders; ImportResolver also catches duplicate triggers, which
                // — like folder creation — needs the store and so can't happen in the pure parsers.
                var folderIdByName: [String: UUID] = [:]
                let resolved = await ImportResolver.resolve(
                    result: result,
                    getExistingByTrigger: { trigger in appState.shortcutStore.getByTrigger(trigger) },
                    resolveFolderId: { name in
                        if let cached = folderIdByName[name] { return cached }
                        let id = appState.folderStore.getOrCreateId(forName: name)
                        folderIdByName[name] = id
                        return id
                    },
                    overwriteDuplicates: overwriteDuplicatesOnImport
                )
                let inserted = appState.shortcutStore.importAll(resolved.toSave)
                importSummary = ImportSummary(importedCount: inserted, skipped: resolved.skipped)
            }
        } catch {
            statusMessage = "Import failed: \(error.localizedDescription)"
        }
    }
}

import SwiftUI
import KeyFlowCore

struct ShortcutListView: View {
    @EnvironmentObject private var appState: AppState
    @State private var query = ""
    @State private var selectedGroup: String? = nil
    @State private var folderFilter: FolderFilter = .all
    @State private var searchScopedToFolder = false
    @State private var editingShortcut: Shortcut? = nil
    @State private var isPresentingAdd = false
    @State private var isPresentingFolders = false

    @State private var selectedIds: Set<UUID> = []
    @State private var showDeleteConfirm = false

    private var isSelectionMode: Bool { !selectedIds.isEmpty }

    private var filtered: [Shortcut] {
        let byGroup = appState.shortcutStore.shortcuts.filter { selectedGroup == nil || $0.group == selectedGroup }

        func matchesFolder(_ s: Shortcut) -> Bool {
            switch folderFilter {
            case .all: return true
            case .uncategorized: return s.folderId == nil
            case .specific(let id): return s.folderId == id
            }
        }
        func matchesQuery(_ s: Shortcut) -> Bool {
            query.isEmpty
                || s.trigger.localizedCaseInsensitiveContains(query)
                || s.expansion.localizedCaseInsensitiveContains(query)
        }

        if query.isEmpty {
            return byGroup.filter(matchesFolder)
        } else if searchScopedToFolder {
            return byGroup.filter { matchesFolder($0) && matchesQuery($0) }
        } else {
            // Matches the Android app: an unscoped search ignores the folder filter entirely,
            // searching across every folder regardless of which chip is selected.
            return byGroup.filter(matchesQuery)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            if !appState.isAccessibilityTrusted {
                AccessibilityStatusView(onEnableClick: AccessibilityPermission.openSystemSettings)
                    .padding([.horizontal, .top])
            }

            HStack {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("Search shortcuts", text: $query)
                    .textFieldStyle(.plain)
            }
            .padding(8)
            .background(Color(nsColor: .textBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .padding()

            ScrollView(.horizontal, showsIndicators: false) {
                HStack {
                    FilterChip(title: "All", isSelected: folderFilter == .all) { folderFilter = .all }
                    FilterChip(title: "Uncategorized", isSelected: folderFilter == .uncategorized) { folderFilter = .uncategorized }
                    ForEach(appState.folderStore.folders) { folder in
                        FilterChip(title: folder.name, isSelected: folderFilter == .specific(folder.id)) {
                            folderFilter = .specific(folder.id)
                        }
                    }
                }
                .padding(.horizontal)
            }
            .padding(.bottom, folderFilter == .all ? 8 : 4)
            // Without an explicit id, this ScrollView/HStack pairing was observed to lag behind
            // `folderStore.folders` updates (stale chip label after a rename) until some other
            // layout pass forced it to redraw — List-based ForEach elsewhere doesn't have this
            // issue, but this manual HStack apparently needs a harder nudge to re-diff.
            .id(appState.folderStore.folders.map { "\($0.id)-\($0.name)" }.joined())

            if folderFilter != .all {
                Toggle("Search this folder only", isOn: $searchScopedToFolder)
                    .toggleStyle(.checkbox)
                    .font(.caption)
                    .padding(.horizontal)
                    .padding(.bottom, 8)
            }

            if !appState.shortcutStore.groups.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack {
                        GroupChip(title: "All groups", isSelected: selectedGroup == nil) { selectedGroup = nil }
                        ForEach(appState.shortcutStore.groups, id: \.self) { group in
                            GroupChip(title: group, isSelected: selectedGroup == group) {
                                selectedGroup = (selectedGroup == group) ? nil : group
                            }
                        }
                    }
                    .padding(.horizontal)
                }
                .padding(.bottom, 8)
            }

            if filtered.isEmpty {
                Spacer()
                Text(query.isEmpty ? "No shortcuts yet. Click + to add one." : "No matches.")
                    .foregroundStyle(.secondary)
                Spacer()
            } else {
                // Plain ScrollView + LazyVStack, not List: macOS's List/NSTableView bridging
                // was reproducibly failing to repaint a row's embedded Toggle (and, less
                // reliably, to remove a deleted row) after an in-place data change — the store
                // update was always correct, confirmed directly, but the row kept showing stale
                // content until something forced the whole List to reconstruct. No amount of
                // `.id()`-based forcing fixed it. LazyVStack has no such reuse/bridging layer.
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(filtered) { shortcut in
                            ShortcutRow(
                                shortcut: shortcut,
                                isSelectionMode: isSelectionMode,
                                isSelected: selectedIds.contains(shortcut.id),
                                onToggle: { enabled in
                                    var updated = shortcut
                                    updated.enabled = enabled
                                    appState.shortcutStore.upsert(updated)
                                },
                                onTap: {
                                    if isSelectionMode {
                                        toggleSelection(shortcut.id)
                                    } else {
                                        editingShortcut = shortcut
                                    }
                                },
                                onLongPress: {
                                    if !isSelectionMode { selectedIds = [shortcut.id] }
                                }
                            )
                            .padding(.horizontal)
                            .padding(.vertical, 6)
                            // Right-click quick-delete outside selection mode — the platform
                            // equivalent of iOS swipe-to-delete, which macOS doesn't have.
                            .contextMenu {
                                if !isSelectionMode {
                                    Button(role: .destructive) {
                                        appState.shortcutStore.delete(shortcut)
                                    } label: {
                                        Label("Delete", systemImage: "trash")
                                    }
                                }
                            }
                            Divider().padding(.leading)
                        }
                    }
                }
            }
        }
        .toolbar {
            if isSelectionMode {
                ToolbarItem(placement: .cancellationAction) {
                    Button(action: { selectedIds = [] }) {
                        Label("Cancel", systemImage: "xmark")
                    }
                }
                ToolbarItem(placement: .principal) {
                    Text("\(selectedIds.count) selected")
                }
                ToolbarItem(placement: .primaryAction) {
                    Button(action: { selectedIds = Set(filtered.map(\.id)) }) {
                        Label("Select All", systemImage: "checkmark.circle")
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button("Uncategorized") { moveSelected(to: nil) }
                        ForEach(appState.folderStore.folders) { folder in
                            Button(folder.name) { moveSelected(to: folder.id) }
                        }
                    } label: {
                        Label("Move to Folder", systemImage: "folder")
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button(role: .destructive, action: { showDeleteConfirm = true }) {
                        Label("Delete", systemImage: "trash")
                    }
                }
            } else {
                ToolbarItem(placement: .primaryAction) {
                    Button(action: { isPresentingFolders = true }) {
                        Label("Folders", systemImage: "folder")
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button(action: { isPresentingAdd = true }) {
                        Label("Add Shortcut", systemImage: "plus")
                    }
                }
            }
        }
        .alert("Delete \(selectedIds.count) shortcut\(selectedIds.count == 1 ? "" : "s")?", isPresented: $showDeleteConfirm) {
            Button("Cancel", role: .cancel) {}
            Button("Delete", role: .destructive) {
                appState.shortcutStore.delete(ids: selectedIds)
                selectedIds = []
            }
        } message: {
            Text("This can't be undone.")
        }
        .sheet(isPresented: $isPresentingAdd) {
            AddEditShortcutView(editing: nil, prefillExpansion: nil)
                .environmentObject(appState)
        }
        .sheet(item: $editingShortcut) { shortcut in
            AddEditShortcutView(editing: shortcut, prefillExpansion: nil)
                .environmentObject(appState)
        }
        .sheet(isPresented: $isPresentingFolders) {
            NavigationStack {
                FoldersView()
                    .environmentObject(appState)
                    .navigationTitle("Folders")
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("Close") { isPresentingFolders = false }
                        }
                    }
            }
        }
        .onAppear { appState.refreshAccessibilityStatus() }
    }

    private func toggleSelection(_ id: UUID) {
        if selectedIds.contains(id) {
            selectedIds.remove(id)
        } else {
            selectedIds.insert(id)
        }
    }

    private func moveSelected(to folderId: UUID?) {
        appState.shortcutStore.moveToFolder(ids: selectedIds, folderId: folderId)
        selectedIds = []
    }
}

private struct ShortcutRow: View {
    let shortcut: Shortcut
    let isSelectionMode: Bool
    let isSelected: Bool
    let onToggle: (Bool) -> Void
    let onTap: () -> Void
    let onLongPress: () -> Void

    var body: some View {
        HStack {
            if isSelectionMode {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? Color.accentColor : Color.secondary)
            }
            // Tap/long-press are scoped to just this label+spacer area, not the whole row —
            // a `.gesture()` on the full row (including the Toggle below) takes hit-testing
            // priority over the Toggle's own tap, silently swallowing clicks meant for it.
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(shortcut.trigger).bold()
                    Text(shortcut.expansion.replacingOccurrences(of: "\n", with: " "))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
            }
            .contentShape(Rectangle())
            // A plain `.onTapGesture` + `.onLongPressGesture` pair on the same view both fire
            // on the mouse-up that ends a long press: the long press enters selection mode,
            // then the tap immediately toggles that same row back off. `.exclusively(before:)`
            // makes only one of the two ever recognize.
            .gesture(
                LongPressGesture(minimumDuration: 0.5)
                    .onEnded { _ in onLongPress() }
                    .exclusively(before: TapGesture().onEnded { onTap() })
            )
            if !isSelectionMode {
                Toggle("", isOn: Binding(get: { shortcut.enabled }, set: onToggle))
                    .labelsHidden()
                    .toggleStyle(.switch)
            }
        }
        .padding(.vertical, 4)
        .background(isSelected ? Color.accentColor.opacity(0.15) : Color.clear)
    }
}

private struct GroupChip: View {
    let title: String
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.caption)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(isSelected ? Color.accentColor : Color.gray.opacity(0.2))
                .foregroundStyle(isSelected ? Color.white : Color.primary)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

private struct FilterChip: View {
    let title: String
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.caption)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(isSelected ? Color.accentColor.opacity(0.85) : Color.gray.opacity(0.2))
                .foregroundStyle(isSelected ? Color.white : Color.primary)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

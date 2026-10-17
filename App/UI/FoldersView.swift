import SwiftUI
import KeyFlowCore

/// Folder management: create/rename/delete with color tags and a per-folder shortcut count.
/// Deleting a folder prompts whether to move its shortcuts to Uncategorized (the default,
/// least-destructive choice) or delete them along with the folder.
struct FoldersView: View {
    @EnvironmentObject private var appState: AppState

    @State private var isPresentingCreate = false
    @State private var folderToRename: Folder?
    @State private var folderToDelete: Folder?

    var body: some View {
        List {
            if appState.folderStore.folders.isEmpty {
                Text("No folders yet. Click + to create one.")
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 24)
            } else {
                ForEach(appState.folderStore.folders) { folder in
                    HStack {
                        ColorDot(colorTag: folder.colorTag)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(folder.name).bold()
                            let count = appState.shortcutStore.count(inFolder: folder.id)
                            Text("\(count) shortcut\(count == 1 ? "" : "s")")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button {
                            folderToRename = folder
                        } label: {
                            Image(systemName: "pencil")
                        }
                        .buttonStyle(.borderless)
                        Button(role: .destructive) {
                            folderToDelete = folder
                        } label: {
                            Image(systemName: "trash")
                        }
                        .buttonStyle(.borderless)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        .frame(minWidth: 420, minHeight: 360)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button(action: { isPresentingCreate = true }) {
                    Label("New Folder", systemImage: "plus")
                }
            }
        }
        .sheet(isPresented: $isPresentingCreate) {
            FolderEditSheet(title: "New Folder", initialName: "", initialColor: nil) { name, color in
                appState.folderStore.createFolder(name: name, colorTag: color)
            }
        }
        .sheet(item: $folderToRename) { folder in
            FolderEditSheet(title: "Rename Folder", initialName: folder.name, initialColor: folder.colorTag) { name, color in
                appState.folderStore.rename(folder, to: name)
                if color != folder.colorTag { appState.folderStore.setColor(folder, to: color) }
            }
        }
        .alert(item: $folderToDelete) { folder in
            let count = appState.shortcutStore.count(inFolder: folder.id)
            return Alert(
                title: Text("Delete \"\(folder.name)\"?"),
                message: Text(
                    count == 0
                        ? "This folder is empty."
                        : "It has \(count) shortcut\(count == 1 ? "" : "s"). Move them to Uncategorized, or delete them along with the folder?"
                ),
                primaryButton: .default(Text("Move to Uncategorized")) {
                    appState.folderStore.deleteFolder(folder, mode: .moveToUncategorized)
                },
                secondaryButton: count > 0
                    ? .destructive(Text("Delete shortcuts too")) {
                        appState.folderStore.deleteFolder(folder, mode: .deleteShortcuts)
                    }
                    : .cancel()
            )
        }
    }
}

private struct ColorDot: View {
    let colorTag: String?

    var body: some View {
        Circle()
            .fill(colorTag.flatMap(Color.init(hex:)) ?? Color.gray.opacity(0.3))
            .frame(width: 14, height: 14)
    }
}

private struct FolderEditSheet: View {
    let title: String
    let initialName: String
    let initialColor: String?
    let onSave: (String, String?) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var color: String?

    init(title: String, initialName: String, initialColor: String?, onSave: @escaping (String, String?) -> Void) {
        self.title = title
        self.initialName = initialName
        self.initialColor = initialColor
        self.onSave = onSave
        _name = State(initialValue: initialName)
        _color = State(initialValue: initialColor)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(title).font(.title2).bold()

            TextField("Folder name", text: $name)
                .textFieldStyle(.roundedBorder)

            HStack(spacing: 8) {
                ForEach(folderColorPalette, id: \.self) { hex in
                    Circle()
                        .fill(Color(hex: hex) ?? .gray)
                        .frame(width: 24, height: 24)
                        .overlay(
                            Circle().stroke(Color.primary, lineWidth: color == hex ? 3 : 0)
                        )
                        .onTapGesture { color = hex }
                }
            }

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Save") {
                    let trimmed = name.trimmingCharacters(in: .whitespaces)
                    if !trimmed.isEmpty { onSave(trimmed, color) }
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 360)
    }
}

extension Color {
    init?(hex: String) {
        var hexString = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        hexString = hexString.replacingOccurrences(of: "#", with: "")
        guard hexString.count == 6, let value = UInt64(hexString, radix: 16) else { return nil }
        let r = Double((value >> 16) & 0xFF) / 255
        let g = Double((value >> 8) & 0xFF) / 255
        let b = Double(value & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}

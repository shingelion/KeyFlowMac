import SwiftUI
import KeyFlowCore

/// Folder assignment control for the Add/Edit sheet: a menu button showing the current folder
/// (or "No folder"), with every existing folder plus a "New Folder…" action that prompts for a
/// name and selects the newly created folder.
struct FolderPicker: View {
    let folders: [Folder]
    let selectedFolderId: UUID?
    let onFolderSelected: (UUID?) -> Void
    let onCreateFolder: (String) -> Void

    @State private var isPresentingNewFolder = false
    @State private var newFolderName = ""

    private var selectedName: String {
        guard let selectedFolderId else { return "No folder" }
        return folders.first { $0.id == selectedFolderId }?.name ?? "No folder"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Folder").font(.caption).foregroundStyle(.secondary)
            Menu {
                Button("No folder") { onFolderSelected(nil) }
                if !folders.isEmpty {
                    Divider()
                    ForEach(folders) { folder in
                        Button(folder.name) { onFolderSelected(folder.id) }
                    }
                }
                Divider()
                Button("New Folder…") {
                    newFolderName = ""
                    isPresentingNewFolder = true
                }
            } label: {
                HStack {
                    Text(selectedName)
                    Spacer()
                    Image(systemName: "chevron.up.chevron.down").font(.caption)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(RoundedRectangle(cornerRadius: 6).stroke(Color.gray.opacity(0.3)))
            }
            .menuStyle(.borderlessButton)
        }
        .alert("New Folder", isPresented: $isPresentingNewFolder) {
            TextField("Folder name", text: $newFolderName)
            Button("Cancel", role: .cancel) {}
            Button("Create") {
                let trimmed = newFolderName.trimmingCharacters(in: .whitespaces)
                if !trimmed.isEmpty { onCreateFolder(trimmed) }
            }
        }
    }
}

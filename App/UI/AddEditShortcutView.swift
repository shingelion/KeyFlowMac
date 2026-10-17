import SwiftUI
import KeyFlowCore

struct AddEditShortcutView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    let editing: Shortcut?
    let prefillExpansion: String?

    @State private var trigger: String = ""
    @State private var expansion: String = ""
    @State private var group: String = ""
    @State private var enabled: Bool = true
    @State private var folderId: UUID? = nil
    @State private var errorMessage: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(editing == nil ? "Add Shortcut" : "Edit Shortcut")
                .font(.title2).bold()

            VStack(alignment: .leading, spacing: 4) {
                Text("Trigger").font(.caption).foregroundStyle(.secondary)
                TextField(";addr", text: $trigger)
                    .textFieldStyle(.roundedBorder)
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("Expansion — use {date}, {time}, {cursor} as placeholders")
                    .font(.caption).foregroundStyle(.secondary)
                TextEditor(text: $expansion)
                    .font(.body)
                    .frame(minHeight: 140)
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.gray.opacity(0.3)))
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("Group (optional)").font(.caption).foregroundStyle(.secondary)
                TextField("", text: $group)
                    .textFieldStyle(.roundedBorder)
            }

            FolderPicker(
                folders: appState.folderStore.folders,
                selectedFolderId: folderId,
                onFolderSelected: { folderId = $0 },
                onCreateFolder: { name in folderId = appState.folderStore.createFolder(name: name).id }
            )

            Toggle("Enabled", isOn: $enabled)

            if let errorMessage {
                Text(errorMessage).foregroundStyle(.red).font(.caption)
            }

            HStack {
                if editing != nil {
                    Button("Delete", role: .destructive) {
                        if let editing { appState.shortcutStore.delete(editing) }
                        dismiss()
                    }
                }
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Save", action: save)
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 420)
        .onAppear {
            if let editing {
                trigger = editing.trigger
                expansion = editing.expansion
                group = editing.group ?? ""
                enabled = editing.enabled
                folderId = editing.folderId
            } else if let prefillExpansion {
                expansion = prefillExpansion
            }
        }
    }

    private func save() {
        let trimmedTrigger = trigger.trimmingCharacters(in: .whitespaces)
        guard !trimmedTrigger.isEmpty else {
            errorMessage = "Trigger can't be empty"
            return
        }
        guard !expansion.isEmpty else {
            errorMessage = "Expansion can't be empty"
            return
        }
        if let existing = appState.shortcutStore.getByTrigger(trimmedTrigger), existing.id != editing?.id {
            errorMessage = "This trigger is already used by another shortcut"
            return
        }

        var shortcut = editing ?? Shortcut(trigger: trimmedTrigger, expansion: expansion)
        shortcut.trigger = trimmedTrigger
        shortcut.expansion = expansion
        shortcut.group = group.isEmpty ? nil : group
        shortcut.enabled = enabled
        shortcut.folderId = folderId
        shortcut.dateModified = Date()

        appState.shortcutStore.upsert(shortcut)
        dismiss()
    }
}

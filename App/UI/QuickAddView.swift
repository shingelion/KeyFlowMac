import SwiftUI
import KeyFlowCore

/// Small, fast add-shortcut form — reached from the menu bar's "+" and from the "Add as
/// KeyFlow Shortcut" Services-menu action on selected text (see ServicesProvider), mirroring
/// the Android app's floating quick-add and "Process text" share action.
struct QuickAddView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var trigger = ""
    @State private var expansion = ""
    @State private var errorMessage: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Add Shortcut").font(.headline)

            TextField("Trigger, e.g. ;addr", text: $trigger)
                .textFieldStyle(.roundedBorder)

            TextEditor(text: $expansion)
                .frame(minHeight: 100)
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.gray.opacity(0.3)))

            if let errorMessage {
                Text(errorMessage).font(.caption).foregroundStyle(.red)
            }

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Save", action: save)
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(16)
        .frame(width: 340)
        .onReceive(NotificationCenter.default.publisher(for: .keyFlowAddAsShortcut)) { note in
            if let text = note.userInfo?["text"] as? String { expansion = text }
        }
    }

    private func save() {
        let trimmed = trigger.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, !expansion.isEmpty else {
            errorMessage = "Both fields are required"
            return
        }
        if appState.shortcutStore.getByTrigger(trimmed) != nil {
            errorMessage = "\"\(trimmed)\" is already used"
            return
        }
        appState.shortcutStore.upsert(Shortcut(trigger: trimmed, expansion: expansion))
        dismiss()
    }
}

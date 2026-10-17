import SwiftUI

/// One-time explainer shown the first time sync is turned on (also reachable any time via
/// "About Syncthing sync…" in Settings).
struct SyncExplainerView: View {
    let onDismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Syncing shortcuts between devices").font(.title2).bold()

            Text("""
            KeyFlow doesn't sync through a server — it just reads and writes one file, \
            keyflow-sync.json, in a folder you choose. Point that folder at one that \
            Syncthing (or Dropbox, a network share, anything that mirrors a folder) is \
            already keeping in sync with your other devices, and KeyFlow keeps that file \
            up to date automatically.
            """)

            VStack(alignment: .leading, spacing: 8) {
                bullet("Pick the same synced folder here that you use on your other device.")
                bullet("Changes you make locally are written to the file a couple seconds after you make them.")
                bullet("Changes that arrive from another device are picked up automatically and merged in — newer edits win, and deletions carry over too.")
                bullet("If nothing appears, check that Syncthing (or whatever you're using) actually has that folder syncing and connected.")
            }

            Spacer(minLength: 0)

            HStack {
                Spacer()
                Button("Got it", action: onDismiss)
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 420, height: 420)
    }

    private func bullet(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text("•")
            Text(text)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .font(.callout)
    }
}

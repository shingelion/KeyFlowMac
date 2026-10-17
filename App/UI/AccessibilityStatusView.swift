import SwiftUI

/// Banner shown wherever the OS-level accessibility permission still needs to be granted —
/// mirrors the Android app's AccessibilityStatusCard.
struct AccessibilityStatusView: View {
    let onEnableClick: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Accessibility permission needed", systemImage: "exclamationmark.triangle.fill")
                .font(.headline)
            Text("KeyFlow can't expand your shortcuts until you allow it in System Settings > Privacy & Security > Accessibility.")
                .font(.subheadline)
            HStack {
                Spacer()
                Button("Open System Settings", action: onEnableClick)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding()
        .background(Color.red.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

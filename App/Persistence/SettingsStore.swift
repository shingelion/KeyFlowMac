import Foundation
import Combine
import KeyFlowCore

/// UserDefaults-backed settings, mirroring the Android app's DataStore-backed SettingsRepository.
@MainActor
final class SettingsStore: ObservableObject {

    private enum Keys {
        static let serviceEnabled = "serviceEnabled"
        static let caseSensitive = "caseSensitive"
        static let preserveCase = "preserveCase"
        static let expansionKey = "expansionKey"
        static let triggerPrefix = "triggerPrefix"
    }

    private let defaults: UserDefaults

    @Published var serviceEnabled: Bool { didSet { defaults.set(serviceEnabled, forKey: Keys.serviceEnabled) } }
    @Published var caseSensitive: Bool { didSet { defaults.set(caseSensitive, forKey: Keys.caseSensitive) } }
    @Published var preserveCase: Bool { didSet { defaults.set(preserveCase, forKey: Keys.preserveCase) } }
    @Published var expansionKey: ExpansionKey { didSet { defaults.set(expansionKey.rawValue, forKey: Keys.expansionKey) } }
    @Published var triggerPrefix: String { didSet { defaults.set(triggerPrefix, forKey: Keys.triggerPrefix) } }

    /// Whether the app shows a Dock icon. Defaults to off (menu-bar-only) — see
    /// `ActivationPolicyManager`. The `didSet` both persists the choice and applies it live, so
    /// toggling in Settings takes effect immediately without a relaunch.
    @Published var showInDock: Bool {
        didSet {
            defaults.set(showInDock, forKey: ActivationPolicyManager.showInDockKey)
            ActivationPolicyManager.apply(showInDock: showInDock)
        }
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        serviceEnabled = defaults.object(forKey: Keys.serviceEnabled) as? Bool ?? true
        caseSensitive = defaults.object(forKey: Keys.caseSensitive) as? Bool ?? false
        preserveCase = defaults.object(forKey: Keys.preserveCase) as? Bool ?? true
        expansionKey = (defaults.string(forKey: Keys.expansionKey)).flatMap(ExpansionKey.init(rawValue:)) ?? .boundaryKey
        triggerPrefix = defaults.string(forKey: Keys.triggerPrefix) ?? ""
        showInDock = ActivationPolicyManager.savedPreference
    }

    var settings: AppSettings {
        AppSettings(
            serviceEnabled: serviceEnabled,
            caseSensitive: caseSensitive,
            preserveCase: preserveCase,
            expansionKey: expansionKey,
            triggerPrefix: triggerPrefix
        )
    }
}

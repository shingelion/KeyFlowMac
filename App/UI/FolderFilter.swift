import Foundation

/// Which slice of the shortcut list is showing — mirrors the Android app's FolderFilter.
enum FolderFilter: Equatable {
    case all
    case uncategorized
    case specific(UUID)
}

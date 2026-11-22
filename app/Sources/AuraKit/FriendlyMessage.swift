import Foundation

/// Plain-language messages for everything that can go wrong. Technical detail goes to the logs,
/// never to the panel.
public enum FriendlyMessage {
    public static let offline = "Aura is offline. Please check your connection."
    public static let reconnecting = "Aura is offline. Reconnecting…"

    /// For a reply that failed mid-turn. Connection status itself is shown by the banner.
    public static func turnFailed(_ error: Error) -> String {
        if describe(error) == offline { return "Aura went offline before answering. Try again once it's back." }
        return describe(error)
    }

    public static func describe(_ error: Error) -> String {
        if let error = error as? AuraClientError {
            switch error {
            case let .http(status, message):
                switch status {
                case 401: return "Aura couldn't confirm this session. Choose Restart Aura from the menu bar icon."
                case 409 where message.localizedCaseInsensitiveContains("approval"):
                    return "Please answer Aura's approval request first."
                case 409: return "Aura is still working on your last request."
                case 422: return "Aura couldn't read that request. Please try again."
                default: return "Aura ran into a problem. Please try again."
                }
            case .badResponse:
                return "Aura ran into a problem. Please try again."
            }
        }
        if let error = error as? URLError {
            switch error.code {
            case .cannotConnectToHost, .networkConnectionLost, .notConnectedToInternet,
                 .cannotFindHost, .timedOut, .dnsLookupFailed:
                return offline
            default:
                break
            }
        }
        if let error = error as? ServerManager.ServerError {
            switch error {
            case .uvNotFound: return "Aura can't start: a required tool (uv) isn't installed."
            case .exitedEarly, .timedOut: return "Aura couldn't start. Choose Restart Aura from the menu bar icon."
            }
        }
        return "Something went wrong. Please try again."
    }
}

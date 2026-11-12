import Foundation
#if os(iOS)
import NetworkExtension
#endif

/// Joining the robot's own Wi-Fi (`PiCrawler-<id>`, WPA2) from the app.
enum Hotspot {
    static let ssidPrefix = "PiCrawler"

    static func ssid(robotID: String) -> String { "\(ssidPrefix)-\(robotID)" }

    /// Are we on a PiCrawler hotspot right now? (needs the wifi-info entitlement
    /// or a network the app configured itself)
    static func currentSSID() async -> String? {
        #if os(iOS)
        return await withCheckedContinuation { continuation in
            NEHotspotNetwork.fetchCurrent { network in continuation.resume(returning: network?.ssid) }
        }
        #else
        return nil
        #endif
    }

    static func onRobotHotspot() async -> Bool {
        await currentSSID()?.hasPrefix(ssidPrefix) ?? false
    }

    /// Ask iOS to join the hotspot. iOS shows a confirmation the first time.
    static func join(robotID: String, passphrase: String) async throws {
        #if os(iOS)
        let configuration = NEHotspotConfiguration(ssid: ssid(robotID: robotID), passphrase: passphrase, isWEP: false)
        configuration.joinOnce = false
        do {
            try await NEHotspotConfigurationManager.shared.apply(configuration)
        } catch let error as NSError where error.domain == NEHotspotConfigurationErrorDomain
                    && error.code == NEHotspotConfigurationError.alreadyAssociated.rawValue {
            return      // already connected: fine
        }
        #else
        throw URLError(.unsupportedURL)
        #endif
    }
}

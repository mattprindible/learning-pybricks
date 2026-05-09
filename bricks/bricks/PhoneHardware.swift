import Combine
import SwiftUI

class PhoneHardwareManager {
    let events = PassthroughSubject<[String: Any], Never>()

    private var cancellables = Set<AnyCancellable>()

    init() {
        UIDevice.current.isBatteryMonitoringEnabled = true

        NotificationCenter.default
            .publisher(for: UIDevice.batteryLevelDidChangeNotification)
            .merge(with: NotificationCenter.default
                .publisher(for: UIDevice.batteryStateDidChangeNotification))
            .sink { [weak self] _ in self?.emitBattery() }
            .store(in: &cancellables)
    }

    func emitCurrentState() {
        emitBattery()
    }

    private func emitBattery() {
        let level = UIDevice.current.batteryLevel
        let state: String
        switch UIDevice.current.batteryState {
        case .charging:  state = "charging"
        case .full:      state = "full"
        case .unplugged: state = "unplugged"
        default:         state = "unknown"
        }
        events.send([
            "type":   "phone_hardware",
            "sensor": "battery",
            "level":  level,
            "state":  state,
        ])
    }
}

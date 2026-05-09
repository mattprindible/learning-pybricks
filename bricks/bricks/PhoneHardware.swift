import Combine
import CoreMotion
import SwiftUI

class PhoneHardwareManager {
    let events = PassthroughSubject<[String: Any], Never>()

    private var cancellables = Set<AnyCancellable>()
    private let motion = CMMotionManager()

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

    func handleCommand(_ command: String) {
        switch command {
        case "start_imu": startIMU()
        case "stop_imu":  stopIMU()
        default: break
        }
    }

    private func startIMU() {
        guard motion.isDeviceMotionAvailable, !motion.isDeviceMotionActive else { return }
        motion.deviceMotionUpdateInterval = 1.0 / 10.0
        motion.startDeviceMotionUpdates(to: .main) { [weak self] data, _ in
            guard let self, let data else { return }
            self.events.send([
                "type":   "phone_hardware",
                "sensor": "imu",
                "accel":  ["x": data.userAcceleration.x, "y": data.userAcceleration.y, "z": data.userAcceleration.z],
                "gyro":   ["x": data.rotationRate.x,     "y": data.rotationRate.y,     "z": data.rotationRate.z],
                "attitude": ["roll": data.attitude.roll, "pitch": data.attitude.pitch, "yaw": data.attitude.yaw],
            ])
        }
    }

    private func stopIMU() {
        motion.stopDeviceMotionUpdates()
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

import Combine
import CoreBluetooth
import Foundation
import OSLog
import UIKit

private let log = Logger(subsystem: "haha.computer.bricks", category: "Hub")

private let pybricksServiceUUID = CBUUID(string: "c5f50001-8280-46da-89f4-6d8051e4aeef")
private let commandEventUUID = CBUUID(string: "c5f50002-8280-46da-89f4-6d8051e4aeef")
private let knownHubUUIDKey = "knownHubUUID"

// Pybricks protocol event types (hub → host, first byte of notification)
private let eventStatusReport: UInt8 = 0x00
private let eventStdout: UInt8 = 0x01

// StatusFlag bit positions
private let flagUserProgramRunning: UInt32 = 1 << 6


enum HubConnectionState: Equatable {
    case unavailable(String)
    case searching
    case connecting
    case reconnecting
    case connected
}

class HubConnectionManager: NSObject, ObservableObject {
    @Published var connectionState: HubConnectionState = .searching
    @Published var hubName: String?
    let stdout = PassthroughSubject<String, Never>()

    private var central: CBCentralManager!
    private var hub: CBPeripheral?
    private var commandChar: CBCharacteristic?
    private var didStartProgram = false
    private var pendingRelease = false
    private var releasingForDeploy = false

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil)
        NotificationCenter.default.addObserver(
            self, selector: #selector(handleWillTerminate),
            name: UIApplication.willTerminateNotification, object: nil)
    }

    @objc private func handleWillTerminate() {
        log.info("App terminating — stopping hub and releasing BLE")
        releaseBLE()
    }

    // Called by server hub_disconnect command — holds reconnect so pybricksdev can take over.
    func releaseForDeploy() {
        guard let peripheral = hub else { return }
        releasingForDeploy = true
        stopAndDisconnect(peripheral)
    }

    func writeStdin(_ text: String) {
        guard let peripheral = hub, let char = commandChar else { return }
        let line = text.hasSuffix("\r\n") ? text : text.hasSuffix("\n") ? text.dropLast() + "\r\n" : text + "\r\n"
        guard let payload = line.data(using: .utf8) else { return }
        var data = Data([0x06])
        data.append(payload)
        log.info("stdin → hub: \(text)")
        peripheral.writeValue(data, for: char, type: .withResponse)
    }

    // Called on background / terminate — releases BLE but allows reconnect if the app returns.
    func releaseBLE() {
        guard let peripheral = hub else { return }
        stopAndDisconnect(peripheral)
    }

    private func stopAndDisconnect(_ peripheral: CBPeripheral) {
        if let char = commandChar {
            pendingRelease = true
            log.info("Sending STOP_USER_PROGRAM before releasing BLE")
            peripheral.writeValue(Data([0x00]), for: char, type: .withResponse)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
                guard let self, self.pendingRelease else { return }
                self.pendingRelease = false
                log.warning("STOP ACK timed out, releasing anyway")
                self.central.cancelPeripheralConnection(peripheral)
            }
        } else {
            central.cancelPeripheralConnection(peripheral)
        }
    }

    private func beginConnectionFlow() {
        if let uuidString = UserDefaults.standard.string(forKey: knownHubUUIDKey),
           let uuid = UUID(uuidString: uuidString),
           let peripheral = central.retrievePeripherals(withIdentifiers: [uuid]).first {
            log.info("Retrieved known peripheral, connecting + scanning")
            hub = peripheral
            hub?.delegate = self
            connectionState = .connecting
            // Cancel any stale pending connection before re-connecting, then
            // scan actively so we catch the hub if it's already advertising.
            central.cancelPeripheralConnection(peripheral)
            central.connect(peripheral)
        } else {
            log.info("No known peripheral, scanning")
            connectionState = .searching
        }
        central.scanForPeripherals(withServices: [pybricksServiceUUID])
    }
}

extension HubConnectionManager: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        log.info("BT state: \(central.state.rawValue)")
        switch central.state {
        case .poweredOn:
            beginConnectionFlow()
        case .poweredOff:
            connectionState = .unavailable("Bluetooth is off")
        case .unauthorized:
            connectionState = .unavailable("Bluetooth permission denied")
        case .unsupported:
            connectionState = .unavailable("Bluetooth not supported on this device")
        case .resetting, .unknown:
            connectionState = .unavailable("Bluetooth unavailable")
        @unknown default:
            connectionState = .unavailable("Bluetooth unavailable")
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String: Any], rssi RSSI: NSNumber) {
        log.info("Discovered: \(peripheral.name ?? "unnamed")")
        central.stopScan()
        hub = peripheral
        hub?.delegate = self
        UserDefaults.standard.set(peripheral.identifier.uuidString, forKey: knownHubUUIDKey)
        connectionState = .connecting
        central.connect(peripheral)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        log.info("Connected: \(peripheral.name ?? "unnamed")")
        hubName = peripheral.name
        connectionState = .connected
        peripheral.discoverServices([pybricksServiceUUID])
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        log.error("Failed to connect: \(error?.localizedDescription ?? "unknown")")
        // Peripheral may be stale; clear and scan fresh.
        UserDefaults.standard.removeObject(forKey: knownHubUUIDKey)
        hub = nil
        connectionState = .searching
        central.scanForPeripherals(withServices: [pybricksServiceUUID])
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        log.info("Disconnected: \(error?.localizedDescription ?? "clean")")
        commandChar = nil
        didStartProgram = false
        pendingRelease = false
        if releasingForDeploy {
            log.info("Holding reconnect — BLE released for deploy")
            connectionState = .searching
            return
        }
        connectionState = .reconnecting
        central.connect(peripheral)
        central.scanForPeripherals(withServices: [pybricksServiceUUID])
    }
}

extension HubConnectionManager: CBPeripheralDelegate {
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error { log.error("discoverServices error: \(error)"); return }
        guard let service = peripheral.services?.first(where: { $0.uuid == pybricksServiceUUID }) else {
            log.error("Pybricks service not found after discovery")
            return
        }
        peripheral.discoverCharacteristics([commandEventUUID], for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        if let error { log.error("discoverCharacteristics error: \(error)"); return }
        guard let characteristic = service.characteristics?.first(where: { $0.uuid == commandEventUUID }) else {
            log.error("Characteristic not found")
            return
        }
        commandChar = characteristic
        peripheral.setNotifyValue(true, for: characteristic)
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        guard pendingRelease else { return }
        pendingRelease = false
        if let error { log.warning("STOP write response: \(error)") }
        log.info("STOP acknowledged — releasing BLE connection")
        central.cancelPeripheralConnection(peripheral)
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard let data = characteristic.value, let eventType = data.first else { return }
        switch eventType {
        case eventStatusReport:
            guard data.count >= 5 else { return }
            let flags = UInt32(data[1]) | UInt32(data[2]) << 8 | UInt32(data[3]) << 16 | UInt32(data[4]) << 24
            let isRunning = flags & flagUserProgramRunning != 0
            log.info("Status report — program running: \(isRunning)")
            if !isRunning && !didStartProgram {
                didStartProgram = true
                log.info("Sending START_USER_PROGRAM")
                peripheral.writeValue(Data([0x01]), for: characteristic, type: .withResponse)
            }
        case eventStdout:
            guard let text = String(data: data.dropFirst(), encoding: .utf8), !text.isEmpty else { return }
            let line = text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !line.isEmpty else { return }
            log.info("Hub stdout: \(line)")
            stdout.send(line)
        default:
            break
        }
    }
}

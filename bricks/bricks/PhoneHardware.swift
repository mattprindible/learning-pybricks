import AVFoundation
import Combine
import CoreLocation
import CoreMotion
import SwiftUI
import Vision

class PhoneHardwareManager: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let events = PassthroughSubject<[String: Any], Never>()

    private var cancellables = Set<AnyCancellable>()
    private let motion = CMMotionManager()
    private var captureSession: AVCaptureSession?
    private let cameraQueue = DispatchQueue(label: "com.bricks.camera", qos: .userInitiated)

    override init() {
        super.init()
        UIDevice.current.isBatteryMonitoringEnabled = true

        NotificationCenter.default
            .publisher(for: UIDevice.batteryLevelDidChangeNotification)
            .merge(with: NotificationCenter.default
                .publisher(for: UIDevice.batteryStateDidChangeNotification))
            .sink { [weak self] _ in self?.emitBattery() }
            .store(in: &cancellables)
    }

    // MARK: - Manifest

    func manifest() -> [String: Any] {
        let device = UIDevice.current
        let motionMgr = CMMotionManager()

        var cameras: [String] = []
        let discoverySession = AVCaptureDevice.DiscoverySession(
            deviceTypes: [.builtInWideAngleCamera, .builtInUltraWideCamera,
                          .builtInTelephotoCamera, .builtInTrueDepthCamera],
            mediaType: .video, position: .unspecified)
        for cam in discoverySession.devices {
            let pos = cam.position == .front ? "front" : "back"
            let type_: String
            switch cam.deviceType {
            case .builtInUltraWideCamera:  type_ = "ultrawide"
            case .builtInTelephotoCamera:  type_ = "tele"
            case .builtInTrueDepthCamera:  type_ = "truedepth"
            default:                       type_ = "wide"
            }
            cameras.append("\(pos)_\(type_)")
        }

        var visionCaps: [String] = ["saliency", "text", "rectangles"]
        if #available(iOS 13.0, *) { visionCaps.append("animals") }
        if #available(iOS 14.0, *) { visionCaps.append("pose") }
        if #available(iOS 15.0, *) { visionCaps.append("hand_pose") }

        let locStatus = CLLocationManager.authorizationStatus()
        let locPermission: String
        switch locStatus {
        case .authorizedAlways, .authorizedWhenInUse: locPermission = "authorized"
        case .denied, .restricted:                    locPermission = "denied"
        default:                                      locPermission = "not_determined"
        }

        let camStatus = AVCaptureDevice.authorizationStatus(for: .video)
        let micStatus = AVCaptureDevice.authorizationStatus(for: .audio)
        func avPerm(_ s: AVAuthorizationStatus) -> String {
            switch s {
            case .authorized:             return "authorized"
            case .denied, .restricted:   return "denied"
            default:                     return "not_determined"
            }
        }

        let motionPerm: String
        switch CMMotionActivityManager.authorizationStatus() {
        case .authorized:             motionPerm = "authorized"
        case .denied, .restricted:   motionPerm = "denied"
        default:                     motionPerm = "not_determined"
        }

        let battery: [String: Any] = [
            "level": device.batteryLevel,
            "state": { switch device.batteryState {
                case .charging:  return "charging"
                case .full:      return "full"
                case .unplugged: return "unplugged"
                default:         return "unknown"
            }}()
        ]

        return [
            "type":   "phone_connected",
            "device": device.model,
            "os":     "\(device.systemName) \(device.systemVersion)",
            "hardware": [
                "gps":        CLLocationManager.locationServicesEnabled(),
                "compass":    CLLocationManager.headingAvailable(),
                "barometer":  CMAltimeter.isRelativeAltitudeAvailable(),
                "motion":     motionMgr.isDeviceMotionAvailable,
                "pedometer":  CMPedometer.isStepCountingAvailable(),
                "cameras":    cameras,
            ],
            "vision_capabilities": visionCaps,
            "permissions": [
                "location":  locPermission,
                "camera":    avPerm(camStatus),
                "microphone": avPerm(micStatus),
                "motion":    motionPerm,
            ],
            "battery": battery,
        ]
    }

    func emitCurrentState() {
        emitBattery()
    }

    func handleCommand(_ command: [String: Any]) {
        guard let action = command["action"] as? String,
              let sensor = command["sensor"] as? String else { return }
        let interval = command["interval"] as? Double ?? 100
        switch (action, sensor) {
        case ("start", "imu"):    startIMU(interval: interval)
        case ("stop",  "imu"):    stopIMU()
        case ("start", "camera"): startCamera()
        case ("stop",  "camera"): stopCamera()
        default: break
        }
    }

    // MARK: - IMU

    private func startIMU(interval: Double = 100) {
        guard motion.isDeviceMotionAvailable, !motion.isDeviceMotionActive else { return }
        motion.deviceMotionUpdateInterval = interval / 1000.0
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

    // MARK: - Camera

    private func startCamera() {
        AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
            guard granted, let self else { return }
            self.cameraQueue.async { self._startCaptureSession() }
        }
    }

    private func stopCamera() {
        cameraQueue.async { [weak self] in
            self?.captureSession?.stopRunning()
            self?.captureSession = nil
        }
    }

    private func _startCaptureSession() {
        let session = AVCaptureSession()
        session.sessionPreset = .vga640x480

        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else { return }
        session.addInput(input)

        // Limit to 10 fps to match the server's default stream interval
        try? device.lockForConfiguration()
        let frameInterval = CMTime(value: 1, timescale: 10)
        device.activeVideoMinFrameDuration = frameInterval
        device.activeVideoMaxFrameDuration = frameInterval
        device.unlockForConfiguration()

        let output = AVCaptureVideoDataOutput()
        output.alwaysDiscardsLateVideoFrames = true
        output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        output.setSampleBufferDelegate(self, queue: cameraQueue)

        guard session.canAddOutput(output) else { return }
        session.addOutput(output)

        // Fix orientation: 0° = landscape right (natural sensor orientation)
        if let connection = output.connection(with: .video),
           connection.isVideoRotationAngleSupported(0) {
            connection.videoRotationAngle = 0
        }

        captureSession = session
        session.startRunning()
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let imageBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }

        CVPixelBufferLockBaseAddress(imageBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(imageBuffer, .readOnly) }

        let width      = CVPixelBufferGetWidth(imageBuffer)
        let height     = CVPixelBufferGetHeight(imageBuffer)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(imageBuffer)
        let baseAddress = CVPixelBufferGetBaseAddress(imageBuffer)

        // kCVPixelFormatType_32BGRA: byteOrder32Little + noneSkipFirst maps B→G→R→A in memory
        let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipFirst.rawValue
                                              | CGBitmapInfo.byteOrder32Little.rawValue)
        guard let ctx = CGContext(data: baseAddress, width: width, height: height,
                                  bitsPerComponent: 8, bytesPerRow: bytesPerRow,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: bitmapInfo.rawValue),
              let cgImage = ctx.makeImage() else { return }

        guard let jpegData = UIImage(cgImage: cgImage).jpegData(compressionQuality: 0.5) else { return }
        let timestampMs = Int64(CMSampleBufferGetPresentationTimeStamp(sampleBuffer).seconds * 1000)
        events.send([
            "type":         "phone_hardware",
            "sensor":       "camera",
            "frame":        jpegData.base64EncodedString(),
            "width":        width,
            "height":       height,
            "timestamp_ms": timestampMs,
        ])
    }

    // MARK: - Battery

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

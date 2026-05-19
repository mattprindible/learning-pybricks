import AVFoundation
import Combine
import CoreLocation
import CoreMotion
import SwiftUI
import Vision

class PhoneHardwareManager: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate, CLLocationManagerDelegate {
    let events = PassthroughSubject<[String: Any], Never>()

    private var cancellables = Set<AnyCancellable>()
    private let motion = CMMotionManager()
    private let altimeter = CMAltimeter()
    private let activityManager = CMMotionActivityManager()
    private var captureSession: AVCaptureSession?
    private var cameraMode: String = "raw"
    private let cameraQueue = DispatchQueue(label: "com.bricks.camera", qos: .userInitiated)
    private lazy var locationManager: CLLocationManager = {
        let m = CLLocationManager()
        m.delegate = self
        m.desiredAccuracy = kCLLocationAccuracyBest
        m.distanceFilter = kCLDistanceFilterNone
        return m
    }()

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

        let locStatus = locationManager.authorizationStatus
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
        case ("start", "imu"):      startIMU(interval: interval)
        case ("stop",  "imu"):      stopIMU()
        case ("start", "camera"):   startCamera(mode: command["mode"] as? String ?? "raw")
        case ("stop",  "camera"):   stopCamera()
        case ("start", "location"):        startLocation()
        case ("stop",  "location"):        stopLocation()
        case ("start", "heading"):         startHeading()
        case ("stop",  "heading"):         stopHeading()
        case ("start", "altimeter"):       startAltimeter()
        case ("stop",  "altimeter"):       stopAltimeter()
        case ("start", "motion_activity"): startMotionActivity()
        case ("stop",  "motion_activity"): stopMotionActivity()
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

    // MARK: - Location + Heading

    private func startLocation() {
        locationManager.requestWhenInUseAuthorization()
        locationManager.startUpdatingLocation()
    }

    private func stopLocation() {
        locationManager.stopUpdatingLocation()
    }

    private func startHeading() {
        guard CLLocationManager.headingAvailable() else { return }
        locationManager.startUpdatingHeading()
    }

    private func stopHeading() {
        locationManager.stopUpdatingHeading()
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        events.send([
            "type":        "phone_hardware",
            "sensor":      "location",
            "lat":         loc.coordinate.latitude,
            "lon":         loc.coordinate.longitude,
            "altitude":    loc.altitude,
            "speed":       loc.speed,
            "course":      loc.course,
            "h_accuracy":  loc.horizontalAccuracy,
            "v_accuracy":  loc.verticalAccuracy,
            "timestamp_ms": Int64(loc.timestamp.timeIntervalSince1970 * 1000),
        ])
    }

    func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        events.send([
            "type":             "phone_hardware",
            "sensor":           "heading",
            "magnetic_heading": newHeading.magneticHeading,
            "true_heading":     newHeading.trueHeading,
            "accuracy":         newHeading.headingAccuracy,
            "x":                newHeading.x,
            "y":                newHeading.y,
            "z":                newHeading.z,
            "timestamp_ms":     Int64(newHeading.timestamp.timeIntervalSince1970 * 1000),
        ])
    }

    // MARK: - Altimeter

    private func startAltimeter() {
        guard CMAltimeter.isRelativeAltitudeAvailable() else { return }
        altimeter.startRelativeAltitudeUpdates(to: .main) { [weak self] data, error in
            guard let self, let data, error == nil else { return }
            self.events.send([
                "type":        "phone_hardware",
                "sensor":      "altimeter",
                "altitude":    data.relativeAltitude.doubleValue,
                "pressure":    data.pressure.doubleValue,
                "timestamp_ms": Int64(Date().timeIntervalSince1970 * 1000),
            ])
        }
    }

    private func stopAltimeter() {
        altimeter.stopRelativeAltitudeUpdates()
    }

    // MARK: - Motion Activity

    private func startMotionActivity() {
        guard CMMotionActivityManager.isActivityAvailable() else { return }
        activityManager.startActivityUpdates(to: .main) { [weak self] activity in
            guard let self, let activity else { return }
            let type: String
            switch true {
            case activity.automotive: type = "automotive"
            case activity.running:    type = "running"
            case activity.walking:    type = "walking"
            case activity.cycling:    type = "cycling"
            case activity.stationary: type = "stationary"
            default:                  type = "unknown"
            }
            let confidence: String
            switch activity.confidence {
            case .high:   confidence = "high"
            case .medium: confidence = "medium"
            default:      confidence = "low"
            }
            self.events.send([
                "type":         "phone_hardware",
                "sensor":       "motion_activity",
                "activity":     type,
                "confidence":   confidence,
                "timestamp_ms": Int64(activity.startDate.timeIntervalSince1970 * 1000),
            ])
        }
    }

    private func stopMotionActivity() {
        activityManager.stopActivityUpdates()
    }

    // MARK: - Camera

    private func startCamera(mode: String = "raw") {
        if captureSession != nil && mode == cameraMode { return }
        if captureSession != nil { stopCamera() }
        cameraMode = mode
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

        let timestampMs = Int64(CMSampleBufferGetPresentationTimeStamp(sampleBuffer).seconds * 1000)
        let mode = cameraMode

        // Vision processing modes are dispatched here
        switch mode {
        case "saliency":
            runSaliency(cgImage: cgImage, width: width, height: height, timestampMs: timestampMs)
        case "animals":
            runAnimals(cgImage: cgImage, width: width, height: height, timestampMs: timestampMs)
        case "text":
            runText(cgImage: cgImage, width: width, height: height, timestampMs: timestampMs)
        case "pose":
            runPose(cgImage: cgImage, width: width, height: height, timestampMs: timestampMs)
        case "hand_pose":
            runHandPose(cgImage: cgImage, width: width, height: height, timestampMs: timestampMs)
        default:
            guard let jpegData = UIImage(cgImage: cgImage).jpegData(compressionQuality: 0.5) else { return }
            events.send([
                "type":         "phone_hardware",
                "sensor":       "camera",
                "mode":         mode,
                "frame":        jpegData.base64EncodedString(),
                "width":        width,
                "height":       height,
                "timestamp_ms": timestampMs,
            ])
        }
    }

    // MARK: - Vision

    // Vision uses bottom-left origin; flip to top-left. Use maxY before flipping because
    // the box's top edge (screen) is its maxY in Vision coords.
    private func visionBBox(_ b: CGRect) -> [String: Any] {
        ["x": b.minX, "y": 1 - b.maxY, "w": b.width, "h": b.height]
    }

    private func runSaliency(cgImage: CGImage, width: Int, height: Int, timestampMs: Int64) {
        let request = VNGenerateAttentionBasedSaliencyImageRequest()
        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return
        }
        guard let result = request.results?.first as? VNSaliencyImageObservation else { return }

        var objects: [[String: Any]] = []
        if let salientObjects = result.salientObjects {
            for obj in salientObjects {
                let b = obj.boundingBox
                objects.append([
                    "confidence": obj.confidence,
                    "bbox": visionBBox(b),
                ])
            }
        }

        events.send([
            "type":            "phone_hardware",
            "sensor":          "camera",
            "mode":            "saliency",
            "salient_objects": objects,
            "width":           width,
            "height":          height,
            "timestamp_ms":    timestampMs,
        ])
    }

    @available(iOS 14.0, *)
    private func _runHandPose(cgImage: CGImage, width: Int, height: Int, timestampMs: Int64) {
        let request = VNDetectHumanHandPoseRequest()
        request.maximumHandCount = 2
        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        do {
            try handler.perform([request])
        } catch { return }

        var hands: [[String: Any]] = []
        for obs in (request.results ?? []) {
            var joints: [String: Any] = [:]
            for jointName in obs.availableJointNames {
                guard let point = try? obs.recognizedPoint(jointName), point.confidence > 0 else { continue }
                // Vision uses bottom-left origin; convert to top-left for agent convenience
                // JointName.rawValue is VNRecognizedPointKey; .rawValue.rawValue reaches the String
                joints[jointName.rawValue.rawValue] = [
                    "x": point.location.x,
                    "y": 1 - point.location.y,
                    "confidence": point.confidence,
                ]
            }
            let chirality: String
            switch obs.chirality {
            case .left:  chirality = "left"
            case .right: chirality = "right"
            default:     chirality = "unknown"
            }
            hands.append(["joints": joints, "chirality": chirality, "confidence": obs.confidence])
        }

        events.send([
            "type":         "phone_hardware",
            "sensor":       "camera",
            "mode":         "hand_pose",
            "hands":        hands,
            "width":        width,
            "height":       height,
            "timestamp_ms": timestampMs,
        ])
    }

    private func runHandPose(cgImage: CGImage, width: Int, height: Int, timestampMs: Int64) {
        if #available(iOS 14.0, *) {
            _runHandPose(cgImage: cgImage, width: width, height: height, timestampMs: timestampMs)
        }
    }

    @available(iOS 14.0, *)
    private func _runPose(cgImage: CGImage, width: Int, height: Int, timestampMs: Int64) {
        let request = VNDetectHumanBodyPoseRequest()
        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return
        }

        // All 19 COCO body keypoints available on VNHumanBodyPoseObservation (iOS 14+)
        let allJointNames: [VNHumanBodyPoseObservation.JointName] = [
            .nose, .leftEye, .rightEye, .leftEar, .rightEar,
            .neck, .leftShoulder, .rightShoulder, .leftElbow, .rightElbow,
            .leftWrist, .rightWrist, .root, .leftHip, .rightHip,
            .leftKnee, .rightKnee, .leftAnkle, .rightAnkle,
        ]

        var bodies: [[String: Any]] = []
        for obs in (request.results ?? []) {
            var joints: [String: Any] = [:]
            for jointName in allJointNames {
                guard let point = try? obs.recognizedPoint(jointName), point.confidence > 0 else { continue }
                // JointName.rawValue is VNRecognizedPointKey; .rawValue.rawValue reaches the String
                // Vision uses bottom-left origin; convert to top-left for agent convenience
                joints[jointName.rawValue.rawValue] = [
                    "x": point.location.x,
                    "y": 1 - point.location.y,
                    "confidence": point.confidence,
                ]
            }
            bodies.append(["joints": joints, "confidence": obs.confidence])
        }

        events.send([
            "type":         "phone_hardware",
            "sensor":       "camera",
            "mode":         "pose",
            "bodies":       bodies,
            "width":        width,
            "height":       height,
            "timestamp_ms": timestampMs,
        ])
    }

    private func runPose(cgImage: CGImage, width: Int, height: Int, timestampMs: Int64) {
        if #available(iOS 14.0, *) {
            _runPose(cgImage: cgImage, width: width, height: height, timestampMs: timestampMs)
        }
    }

    private func runText(cgImage: CGImage, width: Int, height: Int, timestampMs: Int64) {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .fast
        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return
        }

        var texts: [[String: Any]] = []
        for obs in (request.results ?? []) {
            guard let top = obs.topCandidates(1).first else { continue }
            let b = obs.boundingBox
            texts.append([
                "text":       top.string,
                "confidence": top.confidence,
                "bbox":       visionBBox(b),
            ])
        }

        events.send([
            "type":         "phone_hardware",
            "sensor":       "camera",
            "mode":         "text",
            "texts":        texts,
            "width":        width,
            "height":       height,
            "timestamp_ms": timestampMs,
        ])
    }

    private func runAnimals(cgImage: CGImage, width: Int, height: Int, timestampMs: Int64) {
        guard #available(iOS 13.0, *) else { return }
        let request = VNRecognizeAnimalsRequest()
        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return
        }

        var animals: [[String: Any]] = []
        for obs in (request.results ?? []) {
            let b = obs.boundingBox
            animals.append([
                "labels":     obs.labels.map { ["identifier": $0.identifier, "confidence": $0.confidence] },
                "confidence": obs.confidence,
                "bbox":       visionBBox(b),
            ])
        }

        events.send([
            "type":         "phone_hardware",
            "sensor":       "camera",
            "mode":         "animals",
            "animals":      animals,
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

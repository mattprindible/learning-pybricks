import Combine
import Foundation
import Network
import OSLog

private let log = Logger(subsystem: "haha.computer.bricks", category: "Server")
private let cachedURLKey = "serverDirectURL"

enum ServerConnectionState {
    case searching
    case connecting
    case reconnecting
    case connected
}

// Reconnect strategy:
//   Fast path  — URLSession WebSocket to a cached IP:port URL (no mDNS).
//                URL is sourced from the server's "hello" message on first connect
//                and persisted in UserDefaults across launches.
//   Slow path  — NWBrowser + NWConnection used only when no cached URL exists
//                (first-ever launch). Once the server sends its hello the fast
//                path takes over for all future reconnects.

class ServerConnectionManager: NSObject, ObservableObject {
    @Published var connectionState: ServerConnectionState = .searching
    let commands = PassthroughSubject<String, Never>()

    // Slow path (bootstrap only)
    private var browser: NWBrowser?
    private var nwConn: NWConnection?

    // Fast path
    private var wsTask: URLSessionWebSocketTask?
    private var directURL: URL?
    private var directFailCount = 0
    private lazy var session = URLSession(
        configuration: .default, delegate: self, delegateQueue: .main)

    private var restartPending = false

    override init() {
        super.init()
        if let s = UserDefaults.standard.string(forKey: cachedURLKey),
           let u = URL(string: s) {
            directURL = u
            log.info("Cached URL: \(u.absoluteString)")
            openDirect(url: u)
        } else {
            startBrowsing()
        }
    }

    // MARK: - Bonjour discovery (slow path)

    private func startBrowsing() {
        browser?.cancel()
        browser = nil
        restartPending = false
        if wsTask == nil && nwConn == nil {
            connectionState = .searching
        }
        log.info("Browsing for _bricks._tcp.")

        let params = NWParameters()
        params.includePeerToPeer = true
        let b = NWBrowser(for: .bonjour(type: "_bricks._tcp.", domain: nil), using: params)

        b.stateUpdateHandler = { [weak self] state in
            if case .failed(let error) = state {
                log.error("Browser failed: \(error)")
                self?.browser = nil
                self?.scheduleRestart(after: 3)
            }
        }

        b.browseResultsChangedHandler = { [weak self] results, _ in
            guard let self else { return }
            guard let result = results.first else {
                log.info("Service gone")
                let conn = self.nwConn
                self.nwConn = nil
                conn?.cancel()
                if self.wsTask == nil { self.connectionState = .searching }
                return
            }
            log.info("Service found: \(result.endpoint.debugDescription)")
            guard self.wsTask == nil else { return }
            if let url = self.directURL {
                self.openDirect(url: url)
            } else if self.nwConn == nil {
                self.openBonjour(to: result.endpoint)
            }
        }

        b.start(queue: .main)
        browser = b
    }

    private func openBonjour(to endpoint: NWEndpoint) {
        nwConn?.cancel()
        nwConn = nil
        connectionState = .connecting
        log.info("NWConnection → \(endpoint.debugDescription)")

        let wsOpts = NWProtocolWebSocket.Options()
        wsOpts.autoReplyPing = true
        let p = NWParameters.tcp
        p.defaultProtocolStack.applicationProtocols.insert(wsOpts, at: 0)

        let conn = NWConnection(to: endpoint, using: p)
        nwConn = conn
        conn.stateUpdateHandler = { [weak self] state in
            DispatchQueue.main.async { self?.handleBonjourState(state, conn: conn) }
        }
        conn.start(queue: .main)
        receiveNW(on: conn)

        // Guard against silent hangs on stale Bonjour endpoints.
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) { [weak self] in
            guard let self, conn === self.nwConn,
                  self.connectionState == .connecting else { return }
            log.warning("NWConnection timeout")
            self.nwConn = nil
            conn.cancel()
            self.scheduleRestart(after: 1)
        }
    }

    private func handleBonjourState(_ state: NWConnection.State, conn: NWConnection) {
        guard conn === nwConn else { return }
        switch state {
        case .ready:
            log.info("NWConnection ready")
            connectionState = .connected
        case .waiting(let error):
            log.warning("NWConnection waiting: \(error)")
            nwConn = nil; conn.cancel()
            scheduleRestart(after: 2)
        case .failed(let error):
            log.error("NWConnection failed: \(error)")
            nwConn = nil
            scheduleRestart(after: 2)
        case .cancelled:
            nwConn = nil
            scheduleRestart(after: 1)
        default:
            break
        }
    }

    private func scheduleRestart(after delay: Double) {
        guard !restartPending else { return }
        restartPending = true
        if connectionState != .searching { connectionState = .reconnecting }
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.startBrowsing()
        }
    }

    // MARK: - Direct connection (fast path)

    private func openDirect(url: URL) {
        wsTask?.cancel(with: .normalClosure, reason: nil)
        wsTask = nil
        connectionState = .connecting
        log.info("URLSession → \(url.absoluteString)")
        let task = session.webSocketTask(with: url)
        wsTask = task
        task.resume()
        receiveWS(on: task)
    }

    private func scheduleDirectReconnect() {
        guard !restartPending else { return }
        directFailCount += 1
        if directFailCount > 10 {
            // Server may have moved to a new IP — fall back to Bonjour re-discovery.
            log.warning("Direct path failed \(self.directFailCount)×, re-discovering via Bonjour")
            directURL = nil
            UserDefaults.standard.removeObject(forKey: cachedURLKey)
            directFailCount = 0
            startBrowsing()
            return
        }
        restartPending = true
        connectionState = .reconnecting
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            guard let self else { return }
            self.restartPending = false
            guard self.wsTask == nil, let url = self.directURL else { return }
            self.openDirect(url: url)
        }
    }

    // MARK: - Receive

    private func receiveNW(on conn: NWConnection) {
        conn.receiveMessage { [weak self] data, _, _, error in
            guard let self else { return }
            if let error {
                // Already on main queue (conn started with queue: .main).
                guard conn === self.nwConn else { return }
                log.error("NW receive error: \(error)")
                self.nwConn = nil
                conn.cancel()
                if let url = self.directURL {
                    self.openDirect(url: url)
                } else {
                    self.scheduleRestart(after: 2)
                }
                return
            }
            if let data, !data.isEmpty { self.handleMessage(data) }
            if self.nwConn === conn { self.receiveNW(on: conn) }
        }
    }

    private func receiveWS(on task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            guard let self else { return }
            DispatchQueue.main.async {
                guard task === self.wsTask else { return }
                switch result {
                case .success(let msg):
                    if case .string(let t) = msg {
                        guard let data = t.data(using: .utf8) else { break }
                        self.handleMessage(data)
                    }
                    self.receiveWS(on: task)
                case .failure(let error):
                    log.error("WS receive error: \(error)")
                    self.wsTask = nil
                    self.scheduleDirectReconnect()
                }
            }
        }
    }

    private func handleMessage(_ data: Data) {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        log.debug("Server → \(json)")
        // Cache direct URL from server hello for fast future reconnects.
        if directURL == nil,
           let urlStr = json["ws_url"] as? String,
           let url = URL(string: urlStr) {
            directURL = url
            directFailCount = 0
            UserDefaults.standard.set(urlStr, forKey: cachedURLKey)
            log.info("Direct URL cached from hello: \(urlStr)")
        }
        if let type_ = json["type"] as? String, type_ != "hello" {
            commands.send(type_)
        }
    }

    // MARK: - Send

    func send(_ payload: [String: Any]) {
        guard connectionState == .connected,
              let data = try? JSONSerialization.data(withJSONObject: payload),
              let text = String(data: data, encoding: .utf8) else { return }
        if let task = wsTask {
            task.send(.string(text)) { err in
                if let err { log.error("WS send error: \(err)") }
            }
        } else if let conn = nwConn {
            let meta = NWProtocolWebSocket.Metadata(opcode: .text)
            let ctx = NWConnection.ContentContext(identifier: "ws-text", metadata: [meta])
            conn.send(content: data, contentContext: ctx, isComplete: true, completion: .idempotent)
        }
    }
}

// MARK: - URLSessionWebSocketDelegate

extension ServerConnectionManager: URLSessionWebSocketDelegate {
    func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol protocol: String?
    ) {
        guard webSocketTask === wsTask else { return }
        log.info("URLSession WebSocket connected")
        connectionState = .connected
        directFailCount = 0
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        guard task === wsTask else { return }
        if let error {
            log.error("URLSession disconnected: \(error.localizedDescription)")
        } else {
            log.info("URLSession disconnected (clean)")
        }
        wsTask = nil
        scheduleDirectReconnect()
    }
}

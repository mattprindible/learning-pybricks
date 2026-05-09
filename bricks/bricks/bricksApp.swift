import Combine
import SwiftUI

private class AppCoordinator: ObservableObject {
    let hub = HubConnectionManager()
    let server = ServerConnectionManager()
    let phone = PhoneHardwareManager()
    private var cancellables = Set<AnyCancellable>()

    init() {
        hub.stdout
            .sink { [weak self] line in self?.server.send(["type": "hub_stdout", "data": line]) }
            .store(in: &cancellables)
        server.commands
            .sink { [weak self] cmd in
                if cmd == "hub_disconnect" { self?.hub.releaseForDeploy() }
            }
            .store(in: &cancellables)
        server.hubInput
            .sink { [weak self] text in self?.hub.writeStdin(text) }
            .store(in: &cancellables)
        phone.events
            .sink { [weak self] payload in self?.server.send(payload) }
            .store(in: &cancellables)
        server.$connectionState
            .filter { $0 == .connected }
            .sink { [weak self] _ in self?.phone.emitCurrentState() }
            .store(in: &cancellables)
    }
}

@main
struct bricksApp: App {
    @StateObject private var coordinator = AppCoordinator()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView(hub: coordinator.hub, server: coordinator.server)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { coordinator.hub.releaseBLE() }
        }
    }
}

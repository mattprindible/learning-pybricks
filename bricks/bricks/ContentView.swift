import SwiftUI

struct ContentView: View {
    @ObservedObject var hub: HubConnectionManager
    @ObservedObject var server: ServerConnectionManager

    var body: some View {
        VStack(spacing: 24) {
            connectionRow(
                state: hubRowState,
                icon: hubIcon,
                label: hubLabel
            )
            Divider()
            connectionRow(
                state: serverRowState,
                icon: serverIcon,
                label: serverLabel
            )
        }
        .padding()
    }

    // MARK: - Hub row

    private var hubRowState: RowState {
        switch hub.connectionState {
        case .unavailable: return .error
        case .searching, .connecting, .reconnecting: return .working
        case .connected: return .ok
        }
    }

    private var hubIcon: String {
        switch hub.connectionState {
        case .unavailable: return "bluetooth.slash"
        case .searching, .connecting, .reconnecting: return "antenna.radiowaves.left.and.right"
        case .connected: return "checkmark.circle.fill"
        }
    }

    private var hubLabel: String {
        switch hub.connectionState {
        case .unavailable(let reason): return reason
        case .searching: return "Searching for hub..."
        case .connecting: return "Connecting to hub..."
        case .reconnecting: return "Reconnecting to hub..."
        case .connected: return hub.hubName ?? "Hub"
        }
    }

    // MARK: - Server row

    private var serverRowState: RowState {
        switch server.connectionState {
        case .searching, .connecting, .reconnecting: return .working
        case .connected: return .ok
        }
    }

    private var serverIcon: String {
        switch server.connectionState {
        case .searching, .connecting, .reconnecting: return "network"
        case .connected: return "checkmark.circle.fill"
        }
    }

    private var serverLabel: String {
        switch server.connectionState {
        case .searching: return "Searching for server..."
        case .connecting: return "Connecting to server..."
        case .reconnecting: return "Reconnecting to server..."
        case .connected: return "Server"
        }
    }

    // MARK: - Shared row view

    private enum RowState { case working, ok, error }

    @ViewBuilder
    private func connectionRow(state: RowState, icon: String, label: String) -> some View {
        HStack(spacing: 12) {
            if state == .working {
                ProgressView()
            }
            Label(label, systemImage: icon)
                .foregroundStyle(state == .ok ? .green : state == .error ? .red : .primary)
                .font(.title3)
        }
    }
}

#Preview {
    ContentView(hub: HubConnectionManager(), server: ServerConnectionManager())
}

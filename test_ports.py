"""Local client connectivity only; an open port is not evidence of income."""
import socket
def main():
    for label, port in (("Dashboard", 8899), ("Monero daemon", 18081),
                        ("Wallet RPC", 18083), ("Monero ZMQ", 18084),
                        ("XMRig API", 18088), ("P2Pool stratum", 3333)):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                print(label, port, "accepting local connections")
        except OSError:
            print(label, port, "not reachable")
    print("Connectivity does not prove work, payments, or profit.")
if __name__ == "__main__":
    main()


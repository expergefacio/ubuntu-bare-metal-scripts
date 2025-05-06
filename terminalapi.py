import docker
import socket
import threading
from docker import APIClient
from flask import request

# THIS PASTEBIN REPO DEFFO HAS THE WRONG TITLE FOR THIS FILE BUT:
# I spent way too many hours getting this to work, so i feel like
# it deserves being shared
# the purpose of this api is to provide a flask backend
# for xterm.js to connect to the the docker host in a way that mimics docer exec
# to bring a terminal to the frontend

# Initialize the low-level Docker client
# Using the Unix socket shared with the container
client = APIClient(base_url='unix://var/run/docker.sock')

# Globals to track the active session state
docker_socket = None
reader_thread = None
stop_reading = threading.Event()

def init_terminal_handlers(socketio):
    """
    Registers WebSocket handlers for terminal input/output.
    This function is called from the main app and receives the SocketIO instance.
    """

    def get_container_id():
        try:
            container_id = socket.gethostname()
            client.inspect_container(container_id)  # Verifies container exists
            print(f"[get_container_id] Using container ID: {container_id}")
            return container_id
        except Exception as e:
            print(f"[get_container_id] Error: {e}")
            return None

    def read_docker_output():
        global docker_socket
        print("[read_docker_output] Reader thread started.")
        while not stop_reading.is_set() and docker_socket:
            try:
                data = docker_socket.recv(4096)
                if data:
                    socketio.emit('terminal_output', {'output': data.decode(errors='ignore')})
            except Exception as e:
                print(f"[read_docker_output] Error: {e}")
                break
        print("[read_docker_output] Reader thread exiting.")

    @socketio.on('connect')
    def handle_connect(auth=None):
        """
        Starts a shell in the current Docker container when a client connects.
        """
        global docker_socket, reader_thread, stop_reading
        print(f"[connect] Client connected: {request.sid}")

        container_id = get_container_id()
        if not container_id:
            socketio.emit('terminal_output', {'output': 'ERROR: Container not found.\n'})
            return

        try:
            exec_id = client.exec_create(
                container=container_id,
                cmd=['bash', '-i'],
                tty=True,
                stdin=True,
                stdout=True,
                stderr=True
            )["Id"]

            exec_start_result = client.exec_start(exec_id, socket=True, tty=True)
            docker_socket = exec_start_result._sock  # Real socket

            stop_reading.clear()
            reader_thread = threading.Thread(target=read_docker_output, daemon=True)
            reader_thread.start()

            socketio.emit('terminal_output', {'output': '*** Shell session started ***\n'})
        except Exception as e:
            print(f"[connect] Exception: {e}")
            socketio.emit('terminal_output', {'output': f'ERROR: {str(e)}\n'})

    @socketio.on('terminal_input')
    def handle_terminal_input(data):
        """
        Sends input from the frontend to the Docker exec socket.
        """
        global docker_socket
        user_input = data.get('input', '')
        print(f"[terminal_input] Received input: {repr(user_input)}")
        try:
            if docker_socket:
                docker_socket.send(user_input.encode())
            else:
                socketio.emit('terminal_output', {'output': 'ERROR: No active session.\n'})
        except Exception as e:
            print(f"[terminal_input] Error: {e}")
            socketio.emit('terminal_output', {'output': f'ERROR: {str(e)}\n'})

    @socketio.on('disconnect')
    def handle_disconnect():
        """
        Cleanly shuts down the Docker socket on client disconnect.
        """
        global docker_socket, stop_reading
        print(f"[disconnect] Client disconnected: {request.sid}")
        stop_reading.set()
        if docker_socket:
            try:
                docker_socket.close()
                print("[disconnect] Docker socket closed.")
            except Exception as e:
                print(f"[disconnect] Error closing socket: {e}")
            docker_socket = None

    print("[terminalapi] Terminal handlers registered successfully.")

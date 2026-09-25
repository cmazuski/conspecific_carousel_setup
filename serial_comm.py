import serial
import serial.tools.list_ports
import threading
import queue
import time

from protocol import (
    HEADER, MSG_WRITE, MSG_READ, MSG_ACK, MSG_EVENT,
    PACKET_SIZE, build_packet, parse_packet,
)


def list_serial_ports():
    return [p.device for p in serial.tools.list_ports.comports()]


class DeviceConnection:

    def __init__(self, port, baudrate=115200, timeout=1.0, retries=3):
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._retries = retries
        self._serial = None
        self._reader_thread = None
        self._running = False
        self._ack_queue = queue.Queue()
        self._lock = threading.Lock()

        self._event_callbacks = []
        self._ack_callbacks = []
        self._tx_callbacks = []
        self._error_callbacks = []

        self._reader_alive = False
        self._last_error_print = 0.0

    # ---- lifecycle ----

    def connect(self):
        self._serial = serial.Serial(self._port, self._baudrate, timeout=0.1)
        self._running = True
        self._reader_alive = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def disconnect(self):
        self._running = False
        if self._reader_thread:
            self._reader_thread.join(timeout=2.0)
            self._reader_thread = None
        self._reader_alive = False
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    @property
    def is_connected(self):
        return self._serial is not None and self._serial.is_open

    @property
    def is_reader_alive(self):
        """False once the reader thread has died.

        When it dies no further sensor events are ever delivered, so every wait
        on device state blocks forever.  Callers that block on shared state
        should treat this as fatal rather than waiting on a stream that has
        stopped.
        """
        return self._reader_alive

    # ---- callback registration ----

    def on_event(self, cb):
        self._event_callbacks.append(cb)

    def on_ack(self, cb):
        self._ack_callbacks.append(cb)

    def on_tx(self, cb):
        self._tx_callbacks.append(cb)

    def on_error(self, cb):
        self._error_callbacks.append(cb)

    # ---- public API ----

    def write_register(self, register, value):
        packet = build_packet(register, MSG_WRITE, value)
        return self._send_with_retry(packet, register)

    def read_register(self, register):
        packet = build_packet(register, MSG_READ)
        return self._send_with_retry(packet, register)

    # ---- internals ----

    def _report_error(self, msg, throttle=0.0):
        """Surface an error to the registered callbacks *and* to the console.

        Printing unconditionally matters: registering an error callback is
        optional, so anything reported only through the callback list is
        silently discarded whenever a caller forgot to register one — which is
        exactly how a dead reader thread or a stream of ACK timeouts can take
        down a session without leaving a trace.

        `throttle` suppresses repeats for that many seconds, for errors that can
        recur every loop iteration.
        """
        if throttle:
            t = time.time()
            if t - self._last_error_print < throttle:
                return
            self._last_error_print = t
        print(f"[SERIAL] {msg}")
        for cb in self._error_callbacks:
            try:
                cb(msg)
            except Exception:
                pass

    def _send_with_retry(self, packet, register):
        with self._lock:
            # drain stale ACKs
            while not self._ack_queue.empty():
                try:
                    self._ack_queue.get_nowait()
                except queue.Empty:
                    break

            last_err = None
            for attempt in range(self._retries):
                try:
                    self._serial.write(packet)
                    for cb in self._tx_callbacks:
                        cb(packet[1], packet[2], packet[3])
                    ack = self._ack_queue.get(timeout=self._timeout)
                    return ack
                except queue.Empty:
                    last_err = f"Timeout (attempt {attempt + 1}/{self._retries}) for 0x{register:02X}"
                    self._report_error(last_err)

            raise TimeoutError(
                f"No ACK received after {self._retries} attempts for register 0x{register:02X}"
            )

    def _reader_loop(self):
        buf = bytearray()
        while self._running:
            try:
                if self._serial and self._serial.in_waiting:
                    buf.extend(self._serial.read(self._serial.in_waiting))

                while len(buf) >= PACKET_SIZE:
                    try:
                        idx = buf.index(HEADER)
                    except ValueError:
                        buf.clear()
                        break

                    if idx > 0:
                        del buf[:idx]
                    if len(buf) < PACKET_SIZE:
                        break

                    packet = bytes(buf[:PACKET_SIZE])
                    del buf[:PACKET_SIZE]

                    result = parse_packet(packet)
                    if result is None:
                        continue

                    register, msg_type, value = result
                    if msg_type == MSG_ACK:
                        self._ack_queue.put((register, value))
                        for cb in self._ack_callbacks:
                            cb(register, value)
                    elif msg_type == MSG_EVENT:
                        for cb in self._event_callbacks:
                            cb(register, value)

            except serial.SerialException as e:
                if self._running:
                    self._report_error(
                        f"Serial error: {e} — READER THREAD IS STOPPING. No further "
                        f"sensor events will be received; the session cannot advance."
                    )
                break
            except Exception as e:
                if self._running:
                    # Throttled: this can otherwise repeat every 10 ms.
                    self._report_error(f"Read error: {e}", throttle=5.0)

            time.sleep(0.01)

        self._reader_alive = False
        if self._running:
            # Exiting while still nominally running means the loop broke on an
            # error rather than on disconnect() — say so loudly, because every
            # wait on shared sensor state is now permanently stuck.
            self._report_error("Reader thread has exited unexpectedly — "
                               "reconnect required")

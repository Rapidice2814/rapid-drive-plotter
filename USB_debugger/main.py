import sys
import queue

from PySide6.QtWidgets import QApplication

from protocol_codec import Packet, ProtocolCodec, LogPayload, TextPayload
from serial_worker import SerialWorker, SerialConfig
from hdf5_logger import HDF5LogLogger, get_log_filename
from plot_window import PlotWindow


def main():
    app = QApplication(sys.argv)

    command_queue = queue.Queue[bytes]()
    codec = ProtocolCodec()

    print("Available serial ports:")
    ports = SerialWorker.list_available_ports()

    if not ports:
        raise RuntimeError("No serial ports found.")

    for i, port in enumerate(ports, start=1):
        print(f"  {i}) {port}")

    while True:
        choice = input(f"Select a port (1-{len(ports)}): ").strip()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(ports):
                selected_port = ports[idx - 1]
                break
        print("Invalid selection, try again.")

    print(f"Selected port: {selected_port}")

    

    plot = PlotWindow(
        on_command=lambda pkt: command_queue.put(codec.build_packet(codec.encode_packet(pkt)))
    )

    def on_data(rx_buffer: bytearray):
        packets = codec.extract_packets(rx_buffer)
        for packet in packets:
            decoded = codec.decode_packet(packet)
            if decoded is not None and isinstance(decoded.data, LogPayload):
                logger.enqueue(decoded.data)
                plot.enqueue_log(decoded.data)
            if decoded is not None:
                plot.on_reply(decoded)

    worker = SerialWorker(
        config=SerialConfig(port=selected_port, baudrate=115200, timeout=0.1),
        command_queue=command_queue,
        data_callback=on_data,
        error_callback=lambda e: print(f"Serial error: {e}"),
    )

    logger = HDF5LogLogger(
        filename=get_log_filename(),
        batch_size=50,
        flush_interval=0.5,
    )

    # logger.start()
    worker.start()

    try:
        plot.show()
        return app.exec()
    finally:
        worker.stop()
        worker.join()
        logger.stop()
        logger.join()


if __name__ == "__main__":
    raise SystemExit(main())
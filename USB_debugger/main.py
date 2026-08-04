# import queue
# import time

# from protocol_codec import ProtocolCodec, DecodedLogPayload
# from serial_worker import SerialWorker, SerialConfig
# from hdf5_logger import HDF5LogLogger, get_log_filename

# command_queue = queue.Queue()



# codec = ProtocolCodec()


# command_queue.put(codec.build_text_command_packet("stop"))
# command_queue.put(codec.build_text_command_packet("setmask 0x080000EF"))
# command_queue.put(codec.build_text_command_packet("start"))


# ##############SELECT SERIAL PORT###############
# print("Available serial ports:")
# ports = SerialWorker.list_available_ports()

# if not ports:
#     raise RuntimeError("No serial ports found.")

# for i, port in enumerate(ports, start=1):
#     print(f"  {i}) {port}")

# while True:
#     choice = input(f"Select a port (1-{len(ports)}): ").strip()
#     if choice.isdigit():
#         idx = int(choice)
#         if 1 <= idx <= len(ports):
#             selected_port = ports[idx - 1]
#             break
#     print("Invalid selection, try again.")

# print(f"Selected port: {selected_port}")
# #################################################

# logger = HDF5LogLogger(
#     filename=get_log_filename(),
#     batch_size=50,
#     flush_interval=0.5,
# )

# def on_data(rx_buffer: bytearray):
#     packets = codec.extract_packets(rx_buffer)
#     for packet in packets:
#         decoded = codec.decode_packet(packet)
#         if decoded is not None:
#             # print(decoded)
#             if isinstance(decoded.data, DecodedLogPayload):
#                     logger.enqueue(decoded.data)

# worker = SerialWorker(
#     config=SerialConfig(port=selected_port, baudrate=115200, timeout=0.1),
#     command_queue=command_queue,
#     data_callback=on_data,
#     error_callback=lambda e: print(f"Serial error: {e}"),
# )

# logger.start()
# worker.start()

# try:
#     while True:
#         time.sleep(1)
# except KeyboardInterrupt:
#     print("Stopping...")
# finally:
#     worker.stop()
#     worker.join()
#     logger.stop()
#     logger.join()


    #####################


import sys
import queue
import time

from PySide6.QtWidgets import QApplication

from protocol_codec import ProtocolCodec, DecodedLogPayload
from serial_worker import SerialWorker, SerialConfig
from hdf5_logger import HDF5LogLogger, get_log_filename
from plot_window import PlotWindow


def main():
    app = QApplication(sys.argv)

    command_queue = queue.Queue()
    codec = ProtocolCodec()

    # command_queue.put(codec.build_text_command_packet("stop"))
    # command_queue.put(codec.build_text_command_packet("setmask 0x080000EF"))
    # command_queue.put(codec.build_text_command_packet("setmask 0x00080000"))
    # command_queue.put(codec.build_text_command_packet("start"))

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

    

    plot = PlotWindow(on_command=lambda cmd: command_queue.put(codec.build_text_command_packet(cmd)))

    def on_data(rx_buffer: bytearray):
        packets = codec.extract_packets(rx_buffer)
        for packet in packets:
            decoded = codec.decode_packet(packet)
            if decoded is not None and isinstance(decoded.data, DecodedLogPayload):
                logger.enqueue(decoded.data)
                plot.enqueue(decoded.data)

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
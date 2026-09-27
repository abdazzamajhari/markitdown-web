"""Short-lived conversion process: accepts only raw file bytes on stdin."""

import io
import socket
import sys

from markitdown import MarkItDown


def block_network(*args, **kwargs):
    raise OSError("Network disabled during document conversion")


def main() -> int:
    # Defense in depth; run the container without privileged mounts too.
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    socket.socket.connect = block_network
    socket.socket.connect_ex = block_network
    socket.create_connection = block_network
    extension = sys.argv[1]
    try:
        result = MarkItDown(enable_plugins=False).convert_stream(
            io.BytesIO(sys.stdin.buffer.read(10 * 1024 * 1024 + 1)),
            file_extension=extension,
        )
        output = (result.markdown or "").encode("utf-8")
        if len(output) > 2 * 1024 * 1024:
            return 3
        sys.stdout.buffer.write(output)
        return 0
    except Exception:
        # Untrusted converter exceptions can include file contents; don't log them.
        return 2


if __name__ == "__main__":
    sys.exit(main())

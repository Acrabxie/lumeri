# tests_http_harness.py
import http.server
import io
import json
import re
from unittest.mock import MagicMock
import urllib.parse

# This file needs to be at the repo root for tests to import it
# directly without path issues:
# from tests_http_harness import MockSocket, run_server_handler

class MockSocket:
    def __init__(self, raw_request: bytes):
        self._raw_request = raw_request
        self._send_buffer = io.BytesIO()
        self._recv_buffer = io.BytesIO(raw_request)

    def makefile(self, mode, *args, **kwargs):
        if 'w' in mode:
            return self._send_buffer
        if 'r' in mode:
            return self._recv_buffer
        raise ValueError(f"Unsupported mode: {mode}")

    def sendall(self, data):
        self._send_buffer.write(data)

    def getsockname(self):
        # Fake a socket name for the server handler
        return ('127.0.0.1', 80)

    def getpeername(self):
        # Fake a peer name
        return ('127.0.0.1', 12345)

    def close(self):
        pass

    def getvalue(self):
        return self._send_buffer.getvalue()



def create_raw_request(method, path, headers=None, body=None):
    request_line = f"{method} {path} HTTP/1.1"
    header_lines = []
    if headers is None:
        headers = {}
    if 'Host' not in headers and 'host' not in headers:
        headers['Host'] = '127.0.0.1:7788'

    for k, v in headers.items():
        header_lines.append(f"{k}: {v}")

    if body:
        if isinstance(body, dict):
            body = json.dumps(body)
            header_lines.append("Content-Type: application/json")
        if isinstance(body, str):
            body = body.encode('utf-8')
        header_lines.append(f"Content-Length: {len(body)}")
    else:
        body = b""

    full_request = [request_line] + header_lines
    raw_request_str = "\r\n".join(full_request) + "\r\n\r\n"
    encoded_request = raw_request_str.encode('utf-8')
    return encoded_request + body

def run_server_handler(handler_class, raw_request: bytes):
    mock_socket = MockSocket(raw_request)
    handler = handler_class(mock_socket, ('127.0.0.1', 12345), MagicMock())
    response_bytes = mock_socket.getvalue()

    # Parse HTTP response
    response_lines = response_bytes.split(b'\r\n')
    status_line = response_lines[0].decode('utf-8')
    match = re.match(r"HTTP/\d\.\d\s+(\d+)\s+(.*)", status_line)
    if not match:
        raise ValueError(f"Could not parse status line: {status_line}")
    status_code = int(match.group(1))
    status_message = match.group(2)

    headers = {}
    header_start_idx = 1
    for i in range(header_start_idx, len(response_lines)):
        line = response_lines[i]
        if not line: # Empty line signifies end of headers
            break
        header_parts = line.decode('utf-8').split(': ', 1)
        if len(header_parts) == 2:
            headers[header_parts[0].lower()] = header_parts[1]
    
    # Find the end of headers (first occurrence of b'\r\n\r\n')
    header_end_marker = b'\r\n\r\n'
    header_end_idx = response_bytes.find(header_end_marker)

    if header_end_idx != -1:
        body = response_bytes[header_end_idx + len(header_end_marker):]
    else:
        body = b''

    body_content_type = headers.get('content-type', '')
    if 'application/json' in body_content_type and body:
        try:
            body_json = json.loads(body)
        except json.JSONDecodeError:
            body_json = None # Or raise an error, depending on desired strictness
    else:
        body_json = None

    return {
        "status": status_code,
        "status_message": status_message,
        "headers": headers,
        "body": body,
        "body_json": body_json
    }

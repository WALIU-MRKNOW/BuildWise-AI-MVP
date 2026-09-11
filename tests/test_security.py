import unittest

from fastapi import HTTPException, Request, Response

from bot.security import add_security_headers, read_limited_body, validate_json_request


def request_with_body(body: bytes, *, content_type: str = "application/json") -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "scheme": "https",
        "path": "/telegram/webhook",
        "raw_path": b"/telegram/webhook",
        "query_string": b"",
        "headers": [
            (b"content-type", content_type.encode("ascii")),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("buildwise.test", 443),
    }
    return Request(scope, receive)


class RequestValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_limited_body_rejects_stream_over_cap(self) -> None:
        request = request_with_body(b"x" * 11)
        with self.assertRaises(HTTPException) as context:
            await read_limited_body(request, max_bytes=10)
        self.assertEqual(context.exception.status_code, 413)

    async def test_limited_body_accepts_small_payload(self) -> None:
        request = request_with_body(b'{"update_id":1}')
        body = await read_limited_body(request, max_bytes=1024)
        self.assertEqual(body, b'{"update_id":1}')

    async def test_security_headers_are_added(self) -> None:
        request = request_with_body(b"{}")

        async def call_next(_: Request) -> Response:
            return Response(status_code=200)

        response = await add_security_headers(request, call_next)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("max-age=", response.headers["strict-transport-security"])


class ContentTypeTests(unittest.TestCase):
    def test_webhook_requires_json(self) -> None:
        request = request_with_body(b"{}", content_type="text/plain")
        with self.assertRaises(HTTPException) as context:
            validate_json_request(request, max_bytes=1024)
        self.assertEqual(context.exception.status_code, 415)

    def test_content_length_over_limit_is_rejected(self) -> None:
        request = request_with_body(b"x" * 11)
        with self.assertRaises(HTTPException) as context:
            validate_json_request(request, max_bytes=10)
        self.assertEqual(context.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()

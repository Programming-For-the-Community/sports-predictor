"""Unit tests for library.serving.api_response."""
import json

from library.serving.api_response import CORS_HEADERS, json_response


def test_json_body_with_cors_headers():
    response = json_response(404, {"error": "nope"})

    assert response["statusCode"] == 404
    assert response["headers"] is CORS_HEADERS
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
    assert json.loads(response["body"]) == {"error": "nope"}

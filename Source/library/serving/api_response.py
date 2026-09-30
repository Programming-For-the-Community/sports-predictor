"""
The API Gateway proxy response every public read Lambda returns -- a JSON
body with the CORS headers the web app needs.
"""
import json

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
}


def json_response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "headers": CORS_HEADERS, "body": json.dumps(body)}

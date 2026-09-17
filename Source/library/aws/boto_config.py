"""
Shared boto3 client timeout defaults. Every AWS SDK call in this repo runs
inside a Lambda or a short-lived ECS task; botocore's own defaults (60s
connect, 60s read) would let a degraded AWS endpoint eat most of the
execution budget before a call ever fails.
"""
from botocore.config import Config

DEFAULT_CONFIG = Config(connect_timeout=5, read_timeout=10)

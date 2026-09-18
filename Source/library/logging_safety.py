"""Helper for logging a value that may originate outside this project's
own control (an unrecognized Lambda invocation payload, a raw external API
response) without letting it forge extra log lines.
"""


def safe_log_value(value) -> str:
    """repr()'s `value`, then strips any literal newline/carriage-return
    still present -- repr() already escapes those inside individual string
    values, but this makes that guarantee explicit rather than relying on
    the caller knowing repr()'s own escaping behavior."""
    return repr(value).replace("\r", "\\r").replace("\n", "\\n")

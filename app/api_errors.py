"""HTTP-facing error types, separate from model-output retries."""
class RequestBusyError(ValueError):
    pass

class LLMConnectionError(ValueError):
    pass

class HTTPRequestError(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)

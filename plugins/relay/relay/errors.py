"""Errors that cross the store, HTTP, and MCP boundaries."""


class RelayError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def to_dict(self):
        return {"error": self.code, "message": self.message}


class RelayClientError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

class NFCeError(Exception):
    code = "NFCE_ERROR"
    status_code = 422
    retryable = False
    action_required = False

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "action_required": self.action_required,
        }


class InvalidQrCodeError(NFCeError):
    code = "INVALID_QR"
    status_code = 400


class NFCeDocumentMismatchError(NFCeError):
    code = "NFCE_DOCUMENT_MISMATCH"
    status_code = 422


class SefazBlockedError(NFCeError):
    code = "SEFAZ_ACTION_REQUIRED"
    status_code = 424
    action_required = True


class SefazIpBlockedError(SefazBlockedError):
    code = "SEFAZ_IP_BLOCKED"


class SefazNotFoundError(NFCeError):
    code = "NFCE_NOT_FOUND"
    status_code = 404


class SefazTemporaryError(NFCeError):
    code = "SEFAZ_UNAVAILABLE"
    status_code = 503
    retryable = True


class NFCeParseError(NFCeError):
    code = "PARSER_UNSUPPORTED"
    status_code = 422

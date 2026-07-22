class BulkActionError(Exception):
    """Base class for domain errors raised by the service layer. The API
    layer translates these into HTTP responses - services never raise
    HTTPException directly."""


class BulkActionNotFoundError(BulkActionError):
    def __init__(self, bulk_action_id: int):
        self.bulk_action_id = bulk_action_id
        super().__init__(f"Bulk action {bulk_action_id} not found")


class UnsupportedActionTypeError(BulkActionError):
    def __init__(self, action_type: str):
        self.action_type = action_type
        super().__init__(f"Unsupported action type: {action_type}")


class UnsupportedEntityTypeError(BulkActionError):
    def __init__(self, entity_type: str):
        self.entity_type = entity_type
        super().__init__(f"Unsupported entity type: {entity_type}")


class InvalidPayloadError(BulkActionError):
    def __init__(self, detail: str):
        super().__init__(detail)


class BulkActionNotCancellableError(BulkActionError):
    def __init__(self, bulk_action_id: int, status):
        self.bulk_action_id = bulk_action_id
        self.status = status
        super().__init__(
            f"Bulk action {bulk_action_id} cannot be cancelled from status {status}"
        )

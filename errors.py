"""Error de la API compartido por los módulos (server, stock, caja, compras...)."""


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status

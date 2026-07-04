from __future__ import annotations

import logging


class LoggingService:
    def __init__(self) -> None:
        self.logger = logging.getLogger('portfolio-hub.services')

    def info(self, message: str, **context) -> None:
        self.logger.info('%s %s', message, context)

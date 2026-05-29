from typing import Type
from app.config import get_settings
from app.core.exceptions import UnsupportedFormatError
from app.parsers.base import BaseParser

settings = get_settings()


class ParserFactory:
    """
    Polymorphic factory for resolving document parsers by file extension.
    Uses the Strategy Registry Pattern to dynamically register and resolve parsers.
    """

    _registry: dict[str, Type[BaseParser]] = {}

    @classmethod
    def register(cls, extension: str, parser_class: Type[BaseParser]) -> None:
        """
        Register a parser class for a specific file extension.

        Args:
            extension: File extension starting with dot, e.g. '.csv'
            parser_class: Class implementing BaseParser interface.
        """
        cls._registry[extension.lower()] = parser_class

    @classmethod
    def get_parser(cls, extension: str) -> BaseParser:
        """
        Return the correct parser instance for the given file extension.

        Args:
            extension: File extension starting with dot, e.g. '.csv'

        Returns:
            An instance of a class implementing BaseParser.

        Raises:
            UnsupportedFormatError: If the extension is not registered.
        """
        ext = extension.lower()
        if ext not in cls._registry:
            raise UnsupportedFormatError(
                f"No parser registered for file extension '{extension}'"
            )

        parser_cls = cls._registry[ext]

        # Handle max_rows parameter specifically for the CSVParser
        if ext == ".csv":
            return parser_cls(max_rows=settings.max_csv_rows)

        return parser_cls()


# ── Register Default Parsers ──────────────────────────────────────────────────
from app.parsers.csv_parser import CSVParser
from app.parsers.pdf_parser import PDFParser

ParserFactory.register(".csv", CSVParser)
ParserFactory.register(".pdf", PDFParser)

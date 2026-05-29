from app.config import get_settings
from app.core.exceptions import UnsupportedFormatError
from app.parsers.base import BaseParser
from app.parsers.csv_parser import CSVParser
from app.parsers.pdf_parser import PDFParser

settings = get_settings()


class ParserFactory:
    """
    Polymorphic factory for resolving document parsers by file extension.
    """

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
        if ext == ".csv":
            return CSVParser(max_rows=settings.max_csv_rows)
        elif ext == ".pdf":
            return PDFParser()
        else:
            raise UnsupportedFormatError(
                f"No parser registered for file extension '{extension}'"
            )

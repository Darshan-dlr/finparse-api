from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


class BaseParser(ABC, Generic[T]):
    """
    Abstract Base Parser defining the contract for all document parsers.
    
    Implementations must override parse() to parse raw bytes into a structured domain model.
    """

    @abstractmethod
    def parse(self, content: bytes) -> T:
        """
        Parse raw document content bytes.
        
        Args:
            content: The raw file bytes.
            
        Returns:
            The parsed domain dataclass.
        """
        pass

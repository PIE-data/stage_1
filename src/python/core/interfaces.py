from abc import ABC, abstractmethod
from typing import Tuple, Dict, List


# Datalake Intefaces
class Downloader(ABC):
    @abstractmethod
    def download(self, book_id: int) -> Tuple[str, str]:
        """
        Returns a tuple with the header and body text.
        """
        pass

class DatalakeStorage(ABC):
    @abstractmethod
    def save_book(self, book_id: int, header: str, body: str) -> bool:
        """
        Save the header and the body text using any hierarchy (Time-based, Book-based, Batch...)
        """
        pass

# Datamart Intefaces
class MetadataExtractor(ABC):
    @abstractmethod
    def extract(self, header_text: str) -> Dict[str, str]:
        """
        Uses regex to retrieve Author, Title, Language...
        Returns a dictionary with clean data.
        """
        pass

class MetadataRepository(ABC):
    @abstractmethod
    def save(self, book_id: int, metadata: Dict[str, str]) -> bool:
        """
        Persist the metadata in a database (or directory based method) example: SQLiteMetadataRepository
        """
        pass


# Inverted Index Interfaces

class Tokenizer(ABC):
    @abstractmethod
    def tokenize(self, text: str) -> List[str]:
        """
        Removes punctuation marks, lower case and removes stopwords. Returns a list of valid terms.
        """
        pass


class IndexStorage(ABC):
    @abstractmethod
    def save_postings(self, inverted_index: Dict[str, List[int]]) -> bool:
        """
        Receives a dictionary containing a inverted_index (it could be a hashmap, heap...) and persist it in disk.
        (Monolithic JSON, Folders, MongoDB)
        """
        pass


# Layer control inteface
class StateTracker(ABC):
    @abstractmethod
    def is_downloaded(self, book_id: int) -> bool:
        """Check if a book is already downloaded"""
        pass
    
    @abstractmethod
    def is_indexed(self, book_id: int) -> bool:
        """Checks if a book is already indexed"""
        pass

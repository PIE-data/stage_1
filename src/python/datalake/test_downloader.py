from unittest.mock import MagicMock, patch
import pytest

from src.python.datalake.downloader import HEADERS, download


def test_download_success():
    """HTTP 200 returns decoded content with correct headers & URL."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"Hello Gutenberg!"

    with patch("requests.get", return_value=mock_response) as mock_get:
        text = download(1342, source_base="https://test.gutenberg.org")
        assert text == "Hello Gutenberg!"
        mock_get.assert_called_once_with(
            "https://test.gutenberg.org/cache/epub/1342/pg1342.txt",
            headers=HEADERS,
            timeout=(30.0, 60.0),
        )


def test_download_404_never_retries():
    """SPEC.md §2.1: HTTP 404 raises FileNotFoundError immediately without retrying."""
    mock_response = MagicMock()
    mock_response.status_code = 404

    with patch("requests.get", return_value=mock_response) as mock_get:
        with pytest.raises(FileNotFoundError):
            download(99999)
        # Ensure it was called only once (no retries!)
        assert mock_get.call_count == 1


def test_download_retries_on_500_and_recovers():
    res_500 = MagicMock()
    res_500.status_code = 500

    res_200 = MagicMock()
    res_200.status_code = 200
    res_200.content = b"Recovered text"

    with patch("requests.get", side_effect=[res_500, res_200]) as mock_get:
        with patch("time.sleep") as mock_sleep:
            text = download(123)
            assert text == "Recovered text"
            assert mock_get.call_count == 2
            mock_sleep.assert_called_once_with(1.0)


def test_download_exhausted_retries_raises_runtime_error():
    res_500 = MagicMock()
    res_500.status_code = 500

    with patch("requests.get", return_value=res_500):
        with patch("time.sleep"):
            with pytest.raises(RuntimeError):
                download(123)


def test_download_mislabelled_encoding_replacement():
    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_response.content = b"Caf\xe9"

    with patch("requests.get", return_value=mock_response):
        text = download(123)
        assert "Caf" in text

        assert "\ufffd" in text

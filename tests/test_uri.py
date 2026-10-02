import io

import pytest

import tftp
from tftp.uri import TftpURL, download_url, format_url, parse_url, upload_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("tftp://host/file", TftpURL("host", 69, "file")),
        ("tftp://host/path/to/file.bin", TftpURL("host", 69, "path/to/file.bin")),
        ("TFTP://Host:6969/f;mode=netascii", TftpURL("host", 6969, "f", "netascii")),
        ("tftp://[2001:db8::1]:70/boot/x", TftpURL("2001:db8::1", 70, "boot/x")),
        ("tftp://10.0.0.1/a%20b%2Fc", TftpURL("10.0.0.1", 69, "a b/c")),
        ("tftp://h/f;MODE=OCTET", TftpURL("h", 69, "f", "octet")),
    ],
)
def test_parse(url, expected):
    assert parse_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://host/file",
        "tftp:///file",
        "tftp://host/",
        "tftp://host/f;mode=mail",
        "tftp://host/f;x=1",
        "tftp://h:99999/f",
    ],
)
def test_parse_rejects(url):
    with pytest.raises(ValueError):
        parse_url(url)


@pytest.mark.parametrize(
    "target",
    [
        TftpURL("host", 69, "file"),
        TftpURL("2001:db8::1", 6969, "dir/f g", "netascii"),
        TftpURL("10.0.0.1", 70, "a;b"),
    ],
)
def test_format_roundtrip(target):
    assert parse_url(str(target)) == target


def test_format_shape():
    assert format_url("::1", "boot/x", 6969) == "tftp://[::1]:6969/boot/x"
    assert format_url("h", "f", mode="netascii") == "tftp://h/f;mode=netascii"


def test_download_and_upload_by_url(root, make_server):
    server = make_server(root, writable=True)
    base = "tftp://127.0.0.1:%d/" % server.server_address[1]
    sink = io.BytesIO()
    download_url(base + "sub/nested.bin", sink, timeout=0.5)
    assert sink.getvalue() == b"nested"
    upload_url(base + "by-url.txt;mode=netascii", b"a\nb\n", timeout=0.5)
    assert (root / "by-url.txt").read_bytes() == b"a\nb\n"
    assert tftp.parse_url is parse_url

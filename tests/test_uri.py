"""``TFTPURL``: RFC 3617's grammar (``tftpURI = "tftp://" host "/" file [ mode ]``,
``file = *( unreserved / escaped )``) and the conversion and value contracts."""

import copy
import io
import pickle
import subprocess
import sys

import pytest

import tftp
from tftp.exceptions import TFTPValueError
from tftp.uri import TFTPURL, download_url, upload_url


@pytest.mark.parametrize(
    "text,expected",
    [
        # RFC 3617's own examples.
        ("tftp://example.com/mystartupfile", TFTPURL("example.com", 69, "mystartupfile")),
        (
            "tftp://example.com/mystartupfile;mode=netascii",
            TFTPURL("example.com", 69, "mystartupfile", "netascii"),
        ),
        ("tftp://example.com/mystartupfile;mode=octet", TFTPURL("example.com", 69, "mystartupfile")),
        # The scheme, the host and the parameter name are case-insensitive.
        ("TFTP://Host:6969/f;MODE=NETASCII", TFTPURL("host", 6969, "f", "netascii")),
        ("tftp://host/path/to/file.bin", TFTPURL("host", 69, "path/to/file.bin")),
        ("tftp://[2001:db8::1]:70/boot/x", TFTPURL("2001:db8::1", 70, "boot/x")),
        ("tftp://[2001:DB8:0:0:0:0:0:1]/x", TFTPURL("2001:db8::1", 69, "x")),
        ("tftp://10.0.0.1/a%20b%2Fc", TFTPURL("10.0.0.1", 69, "a b/c")),
        ("tftp://h/a%3Bb", TFTPURL("h", 69, "a;b")),
        ("tftp://h/a%3Fb%23c", TFTPURL("h", 69, "a?b#c")),
        ("tftp://h/a%25b", TFTPURL("h", 69, "a%b")),
        ("tftp://h:/f", TFTPURL("h", 69, "f")),
        # An RFC 6874 zone decodes its %25; the bare spelling is read too.
        ("tftp://[fe80::1%25eth0]/f", TFTPURL("fe80::1%eth0", 69, "f")),
        ("tftp://[fe80::1%eth0]/f", TFTPURL("fe80::1%eth0", 69, "f")),
        # Octets that are not UTF-8 are carried, as the codec carries them.
        ("tftp://h/caf%E9", TFTPURL("h", 69, "caf\udce9")),
        ("tftp://h/caf%C3%A9", TFTPURL("h", 69, "caf\xe9")),
    ],
)
def test_parse(text, expected):
    assert TFTPURL.parse(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "http://host/file",
        "host/file",
        "",
        "tftp:///file",
        "tftp://host",
        "tftp://host/",
        # No place in the grammar: a query, a fragment, userinfo.
        "tftp://h/boot.cfg?mac=00-11",
        "tftp://h/menu#top",
        "tftp://h?x/f",
        "tftp://user:secret@h/f",
        "tftp://user@h/f",
        # Ports.
        "tftp://h:0/f",
        "tftp://h:99999/f",
        "tftp://h:+70/f",
        "tftp://h:7_0/f",
        "tftp://h:7x/f",
        "tftp://[::1]x/f",
        "tftp://::1/f",
        "tftp://[::1/f",
        "tftp://[nonsense]/f",
        "tftp://[fe80::1%]/f",
        # Parameters.
        "tftp://h/f;mode=mail",
        "tftp://h/f;mode=",
        "tftp://h/f;mode",
        "tftp://h/f;x=1",
        "tftp://h/f;",
        "tftp://h/f;mode=octet;mode=netascii",
        "tftp://h/f;mode=octet;mode=octet",
        # File names the codec cannot carry, and control characters.
        "tftp://h/a%00b",
        "tftp://h/a\nb",
        "tftp://h\t/f",
        "tftp://h%41/f",
    ],
)
def test_parse_rejects(text):
    with pytest.raises(TFTPValueError):
        TFTPURL.parse(text)
    assert TFTPURL.try_parse(text) is None


def test_parse_wrong_types_are_type_errors():
    for bad in (None, b"tftp://h/f", 5, ("tftp://h/f",)):
        with pytest.raises(TypeError):
            TFTPURL.parse(bad)
        with pytest.raises(TypeError):
            TFTPURL.try_parse(bad)


def test_try_parse_returns_the_default_for_bad_text():
    marker = TFTPURL("h", 69, "f")
    assert TFTPURL.try_parse("tftp://h/f?x", marker) is marker
    assert TFTPURL.try_parse("tftp://h/f") == marker


@pytest.mark.parametrize(
    "target",
    [
        TFTPURL("host", 69, "file"),
        TFTPURL("host", 1, "file"),
        TFTPURL("host", 65535, "file"),
        TFTPURL("2001:db8::1", 6969, "dir/f g", "netascii"),
        TFTPURL("fe80::1%eth0", 70, "x"),
        TFTPURL("fe80::1%a%25b", 70, "x"),
        TFTPURL("10.0.0.1", 70, "a;b"),
        TFTPURL("h", 69, "a?b#c%d"),
        TFTPURL("h", 69, "caf\udce9\udcff"),
        TFTPURL("h", 69, "\xe9\u4e2d\U0001f600"),
        TFTPURL("h", 69, "/absolute"),
        TFTPURL("h", 69, ".."),
    ],
)
def test_text_round_trip(target):
    assert TFTPURL.parse(str(target)) == target
    assert str(TFTPURL.parse(str(target))) == str(target)


def test_text_shape():
    assert str(TFTPURL("::1", 6969, "boot/x")) == "tftp://[::1]:6969/boot/x"
    assert str(TFTPURL("h", 69, "f", "netascii")) == "tftp://h/f;mode=netascii"
    assert str(TFTPURL("fe80::1%eth0", 69, "f")) == "tftp://[fe80::1%25eth0]/f"
    assert str(TFTPURL("h", 69, "a b?#;%")) == "tftp://h/a%20b%3F%23%3B%25"
    assert str(TFTPURL("h", 69, "caf\udce9")) == "tftp://h/caf%E9"


def test_the_constructor_normalises():
    assert TFTPURL("H", 69, "f", "NetAscii") == TFTPURL("h", 69, "f", "netascii")
    assert TFTPURL("[::1]", 69, "f") == TFTPURL("::1", 69, "f")
    assert TFTPURL("2001:DB8:0::1", 69, "f").host == "2001:db8::1"
    assert hash(TFTPURL("H", 69, "f")) == hash(TFTPURL("h", 69, "f"))
    assert TFTPURL("fe80::1%Eth0", 69, "f").host == "fe80::1%Eth0"  # a zone is case-sensitive


@pytest.mark.parametrize(
    "args",
    [
        ("h", 0, "f"),
        ("h", 65536, "f"),
        ("h", -1, "f"),
        ("h", 69, ""),
        ("h", 69, "a\0b"),
        ("h", 69, "f", "mail"),
        ("h", 69, "f", ""),
        ("", 69, "f"),
        ("a/b", 69, "f"),
        ("a b", 69, "f"),
        ("u@h", 69, "f"),
        ("h?", 69, "f"),
        ("h%41", 69, "f"),
        ("::g", 69, "f"),
        ("fe80::1%", 69, "f"),
    ],
)
def test_the_constructor_refuses_what_no_url_can_carry(args):
    with pytest.raises(TFTPValueError):
        TFTPURL(*args)


@pytest.mark.parametrize(
    "args",
    [
        ("h", "70", "f"),
        ("h", True, "f"),
        ("h", 69.0, "f"),
        ("h", 69, b"f"),
        ("h", 69, None),
        ("h", 69, "f", None),
        ("h", 69, "f", b"octet"),
        (None, 69, "f"),
        (b"h", 69, "f"),
        (5, 69, "f"),
    ],
)
def test_the_constructor_refuses_a_wrong_type(args):
    with pytest.raises(TypeError):
        TFTPURL(*args)


def test_a_host_object_is_reduced_to_its_text():
    import ipaddress

    import netimps

    assert TFTPURL(ipaddress.ip_address("2001:db8::1"), 70, "f").host == "2001:db8::1"
    assert TFTPURL(ipaddress.ip_interface("10.0.0.5/24"), 69, "f").host == "10.0.0.5"
    assert TFTPURL(netimps.Host("Boot.Lan"), 69, "f").host == "boot.lan"


def test_it_is_a_value():
    url = TFTPURL("h", 70, "f", "netascii")
    same = TFTPURL("H", 70, "f", "NETASCII")
    assert url == same and hash(url) == hash(same) and len({url, same}) == 1
    assert url != TFTPURL("h", 69, "f", "netascii") and url != TFTPURL("h", 70, "g", "netascii")
    assert url != TFTPURL("h", 70, "f") and url != TFTPURL("i", 70, "f", "netascii")
    with pytest.raises(AttributeError):
        url.port = 71  # type: ignore[misc]
    with pytest.raises(AttributeError):
        url.nothing = 1  # type: ignore[attr-defined]
    assert eval(repr(url), {"TFTPURL": TFTPURL}) == url
    assert copy.copy(url) == url and type(copy.copy(url)) is TFTPURL
    assert copy.deepcopy(url) == url
    assert pickle.loads(pickle.dumps(url)) == url and type(pickle.loads(pickle.dumps(url))) is TFTPURL


def test_it_equals_nothing_of_another_type():
    url = TFTPURL("h", 69, "f", "octet")
    assert url.__eq__(("h", 69, "f", "octet")) is NotImplemented
    assert url.__eq__("tftp://h/f") is NotImplemented
    assert url != ("h", 69, "f", "octet") and url != "tftp://h/f"
    assert ("h", 69, "f", "octet") != url
    assert url not in {("h", 69, "f", "octet")}
    with pytest.raises(TypeError):
        tuple(url)  # not a tuple: no unpacking


def test_formatting_a_url_imports_no_netimps():
    code = (
        "import sys, tftp\n"
        "u = tftp.TFTPURL('h', 70, 'f', 'netascii')\n"
        "assert str(u) == 'tftp://h:70/f;mode=netascii'\n"
        "assert tftp.TFTPURL.parse(str(u)) == u\n"
        "print('netimps' in sys.modules)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "False"


def test_download_and_upload_by_url(root, make_server):
    server = make_server(root, writable=True)
    base = "tftp://127.0.0.1:%d/" % server.server_address[1]
    sink = io.BytesIO()
    download_url(base + "sub/nested.bin", sink, timeout=0.5)
    assert sink.getvalue() == b"nested"
    upload_url(base + "by-url.txt;mode=netascii", b"a\nb\n", timeout=0.5)
    assert (root / "by-url.txt").read_bytes() == b"a\nb\n"
    assert tftp.TFTPURL is TFTPURL

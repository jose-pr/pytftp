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
        # No place in the grammar: a fragment, userinfo.
        "tftp://h/boot.cfg?mac",
        "tftp://h/menu#top",
        "tftp://h?x/f",
        "tftp://user:secret@h/f",
        "tftp://user@h/f",
        # Ports.
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


# -- Transfer options, in either spelling; port 0 is the default port ---------------


def _url(options=None, mode="octet", filename="f"):
    return TFTPURL("h", 69, filename, mode, options)


def test_port_zero_is_the_default_port():
    assert TFTPURL("h", 0, "f") == TFTPURL("h", 69, "f")
    assert TFTPURL("h", 0, "f").port == 69
    assert TFTPURL.parse("tftp://h:0/f") == TFTPURL("h", 69, "f")
    assert TFTPURL.parse("tftp://[::1]:0/f;blksize=512") == TFTPURL(
        "::1", 69, "f", options={"blksize": "512"}
    )
    assert str(TFTPURL.parse("tftp://h:0/f")) == "tftp://h/f"


@pytest.mark.parametrize(
    "text,expected",
    [
        # The first of ? and ; after the file name decides the spelling.
        ("tftp://h/f?blksize=1428&windowsize=16", _url({"blksize": "1428", "windowsize": "16"})),
        ("tftp://h/f;blksize=1428;windowsize=16", _url({"blksize": "1428", "windowsize": "16"})),
        ("tftp://h/f?mode=netascii&blksize=512", _url({"blksize": "512"}, "netascii")),
        ("tftp://h/f;mode=netascii;blksize=512", _url({"blksize": "512"}, "netascii")),
        ("tftp://h/f?blksize=512&mode=NetAscii", _url({"blksize": "512"}, "netascii")),
        ("tftp://h/f?mode=netascii", _url(None, "netascii")),
        ("tftp://h/f?blksize=mtu", _url({"blksize": "mtu"})),
        ("tftp://h/f;blksize=MTU", _url({"blksize": "MTU"})),
        ("tftp://h/f?timeout=3&tsize=0&rollover=1", _url({"timeout": "3", "tsize": "0", "rollover": "1"})),
        ("tftp://h/f;timeout=0.5;tsize=true", _url({"timeout": "0.5", "tsize": "true"})),
        # A name that is not a known one is a wire option, requested verbatim.
        ("tftp://h/f?cookie=abc", _url({"cookie": "abc"})),
        ("tftp://h/f;cookie=abc;x-list=1", _url({"cookie": "abc", "x-list": "1"})),
        ("tftp://h/f?empty=", _url({"empty": ""})),
        ("tftp://h/boot.cfg?mac=00-11", _url({"mac": "00-11"}, filename="boot.cfg")),
        # Names are compared without case and stored lower-case.
        ("tftp://h/f?BlkSize=512&X-Y=Z", _url({"blksize": "512", "x-y": "Z"})),
        ("tftp://h/f;MODE=netascii;WindowSize=4", _url({"windowsize": "4"}, "netascii")),
        # A percent-encoded delimiter is part of a value, in both spellings.
        ("tftp://h/f?x=a%3Bb%26c%3Fd%3De%23f", _url({"x": "a;b&c?d=e#f"})),
        ("tftp://h/f;x=a%3Bb%26c%3Fd%3De%23f", _url({"x": "a;b&c?d=e#f"})),
        ("tftp://h/f?x%3D%26=1", _url({"x=&": "1"})),
        ("tftp://h/f?x=a%20b%25%C3%A9", _url({"x": "a b%\xe9"})),
        ("tftp://h/f?x=caf%E9", _url({"x": "caf\udce9"})),
        # The other spelling's delimiter is plain text after the first one decided.
        ("tftp://h/f;x=a&b", _url({"x": "a&b"})),
        ("tftp://h/f?x=a,b=c", _url({"x": "a,b=c"})),
        ("tftp://h:0/f?blksize=512", _url({"blksize": "512"})),
        ("tftp://h/dir/a%3Bb;blksize=512", _url({"blksize": "512"}, filename="dir/a;b")),
        ("tftp://h/a%3Fb?blksize=512", _url({"blksize": "512"}, filename="a?b")),
    ],
)
def test_parse_options(text, expected):
    assert TFTPURL.parse(text) == expected
    assert hash(TFTPURL.parse(text)) == hash(expected)


@pytest.mark.parametrize(
    "text",
    [
        # Both delimiters unencoded, in either order.
        "tftp://h/f?a=1;b=2",
        "tftp://h/f;a=1?b=2",
        "tftp://h/f?a=1&b=2;c=3",
        "tftp://h/f;mode=netascii?blksize=1428",
        "tftp://h/f?mode=netascii;blksize=1428",
        "tftp://h/f?a=1?b=2",
        # A repeated name, in either case, in either spelling.
        "tftp://h/f?a=1&a=2",
        "tftp://h/f?a=1&A=2",
        "tftp://h/f;a=1;A=1",
        "tftp://h/f?mode=octet&MODE=octet",
        "tftp://h/f?blksize=512&BlkSize=512",
        # An empty pair, a pair with no =, an empty name.
        "tftp://h/f?",
        "tftp://h/f?a=1&",
        "tftp://h/f?&a=1",
        "tftp://h/f?a=1&&b=2",
        "tftp://h/f;;a=1",
        "tftp://h/f;a=1;",
        "tftp://h/f?a",
        "tftp://h/f?a=1&b",
        "tftp://h/f;a",
        "tftp://h/f?=1",
        "tftp://h/f;=1",
        # A value the known name cannot read, or the client would refuse.
        "tftp://h/f?blksize=abc",
        "tftp://h/f?blksize=",
        "tftp://h/f?blksize=7",
        "tftp://h/f?blksize=65465",
        "tftp://h/f?blksize=+512",
        "tftp://h/f?blksize=%20512",
        "tftp://h/f?blksize=1_000",
        "tftp://h/f?blksize=%D9%A5%D9%A1%D9%A2",
        "tftp://h/f?windowsize=0",
        "tftp://h/f?windowsize=65536",
        "tftp://h/f?windowsize=mtu",
        "tftp://h/f?rollover=2",
        "tftp://h/f?tsize=maybe",
        "tftp://h/f?tsize=2",
        "tftp://h/f?timeout=-1",
        "tftp://h/f?timeout=0",
        "tftp://h/f?timeout=abc",
        "tftp://h/f?timeout=1e3",
        "tftp://h/f?timeout=nan",
        "tftp://h/f?timeout=.5",
        "tftp://h/f?mode=mail",
        "tftp://h/f?mode=",
        # A fragment, a NUL or a control character in a name or a value.
        "tftp://h/f?a=1#top",
        "tftp://h/f;a=1#top",
        "tftp://h/f#top?a=1",
        "tftp://h/f?a=%00",
        "tftp://h/f?%00=1",
        "tftp://h/f?a=%0A",
        "tftp://h/f;a=%1F",
        "tftp://h/f;a%7F=1",
        "tftp://h/f?a=1\nb",
        "tftp://h/f?a\t=1",
    ],
)
def test_parse_options_rejects(text):
    with pytest.raises(TFTPValueError):
        TFTPURL.parse(text)
    assert TFTPURL.try_parse(text) is None


def test_the_options_are_a_read_only_mapping_in_the_value():
    url = TFTPURL("h", 69, "f", options={"BlkSize": "512", "x": 7})
    assert dict(url.options) == {"blksize": "512", "x": "7"}
    assert TFTPURL("h", 69, "f").options == {}
    with pytest.raises(TypeError):
        url.options["x"] = "y"  # type: ignore[index]
    source = {"x": "1"}
    held = TFTPURL("h", 69, "f", options=source)
    source["x"] = "2"
    assert held.options["x"] == "1"
    other = TFTPURL("h", 69, "f", options={"x": "7", "blksize": "512"})
    assert url == other and hash(url) == hash(other) and len({url, other}) == 1
    assert url != TFTPURL("h", 69, "f") and url != TFTPURL("h", 69, "f", options={"blksize": "513", "x": "7"})
    assert url.__eq__(dict(url.options)) is NotImplemented
    assert eval(repr(url), {"TFTPURL": TFTPURL}) == url
    assert "options" not in repr(TFTPURL("h", 69, "f")) and "{" not in repr(TFTPURL("h", 69, "f"))
    assert copy.copy(url) == url and copy.deepcopy(url) == url
    assert pickle.loads(pickle.dumps(url)) == url
    with pytest.raises(AttributeError):
        url.options = {}  # type: ignore[misc]


@pytest.mark.parametrize(
    "options,error",
    [
        ({"mode": "octet"}, TFTPValueError),
        ({"": "1"}, TFTPValueError),
        ({"a\0b": "1"}, TFTPValueError),
        ({"a": "1\0"}, TFTPValueError),
        ({"a": "1\n"}, TFTPValueError),
        ({"a": "1", "A": "2"}, TFTPValueError),
        ({"blksize": "abc"}, TFTPValueError),
        ({"windowsize": "0"}, TFTPValueError),
        ({"windowsize": 0}, TFTPValueError),
        ({1: "1"}, TypeError),
        ({"a": None}, TypeError),
        ({"a": b"1"}, TypeError),
        ({"a": True}, TypeError),
        ({"a": 1.5}, TypeError),
        ([("a", "1")], TypeError),
        ("a=1", TypeError),
    ],
)
def test_the_constructor_refuses_bad_options(options, error):
    with pytest.raises(error):
        TFTPURL("h", 69, "f", options=options)


def test_options_text_shape():
    # str() writes the ; spelling: mode first and only when it is not octet,
    # then the options in name order.
    url = TFTPURL("h", 69, "f", "netascii", {"windowsize": "16", "blksize": "1428", "Cookie": "a b"})
    assert str(url) == "tftp://h/f;mode=netascii;blksize=1428;cookie=a%20b;windowsize=16"
    assert (
        str(TFTPURL("h", 70, "f", options={"x": "a;b&c?d=e#f%"}))
        == "tftp://h:70/f;x=a%3Bb%26c%3Fd%3De%23f%25"
    )
    assert str(TFTPURL.parse("tftp://h/f?blksize=8&mode=netascii")) == "tftp://h/f;mode=netascii;blksize=8"
    mode_only = "tftp://h/f;mode=netascii"
    assert str(TFTPURL.parse(mode_only)) == mode_only  # exactly RFC 3617's form
    assert str(TFTPURL.parse("tftp://h/f?mode=octet")) == "tftp://h/f"


_AWKWARD = [
    "a;b",
    "a&b",
    "a?b",
    "a=b",
    "a#b",
    "a%b",
    "%41",
    "%",
    "a b",
    " ",
    "+",
    "/",
    "a/b",
    "\xe9中\U0001f600",
    "caf\udce9\udcff",
    "x" * 200,
    "",
    ";",
    "&",
    "?",
    "=",
    "#",
]


@pytest.mark.parametrize("value", _AWKWARD)
def test_option_values_round_trip(value):
    for filename in ("f", "a;b?c", "dir/f"):
        for mode in ("octet", "netascii"):
            url = TFTPURL("h", 70, filename, mode, {"x": value, "Other": "1", "blksize": "512"})
            again = TFTPURL.parse(str(url))
            assert again == url and hash(again) == hash(url) and again.options["x"] == value
            assert str(again) == str(url)


@pytest.mark.parametrize("name", [n for n in _AWKWARD if n] + ["MixedCase", "X-Y", "UPPER"])
def test_option_names_round_trip(name):
    url = TFTPURL("h", 69, "f", options={name: "v", "z": "1"})
    again = TFTPURL.parse(str(url))
    assert again == url and hash(again) == hash(url)
    assert name.lower() in again.options and str(again) == str(url)


@pytest.mark.parametrize("text", ["tftp://h/f?blksize=512&x=a%3Bb", "tftp://h/f;x=a&b;mode=netascii"])
def test_each_spelling_parses_back_through_str(text):
    url = TFTPURL.parse(text)
    assert TFTPURL.parse(str(url)) == url


def test_the_known_names_are_readable_values():
    from tftp.uri import _client_keywords

    url = TFTPURL(
        "h",
        69,
        "f",
        options={
            "blksize": "mtu",
            "windowsize": "16",
            "timeout": "2.5",
            "tsize": "false",
            "rollover": "1",
            "cookie": "x",
            "x-other": "7",
        },
    )
    assert _client_keywords(url) == {
        "blksize": "mtu",
        "windowsize": 16,
        "timeout": 2.5,
        "tsize": False,
        "rollover": 1,
        "extra_options": {"cookie": "x", "x-other": "7"},
    }
    assert _client_keywords(TFTPURL("h", 69, "f")) == {}
    assert _client_keywords(TFTPURL("h", 69, "f", options={"blksize": "512", "tsize": "1"})) == {
        "blksize": 512,
        "tsize": True,
    }

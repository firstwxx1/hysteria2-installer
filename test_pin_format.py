"""Regression test: pinSHA256 must be HEX, and only for self-signed certs."""
import subprocess
import tempfile
from pathlib import Path

import portal


def meta(ins, pin):
    return dict(public_ip="1.2.3.4", server_name="tw.example.com", listen_port=19984,
                auth_password="pw", obfs_password="obfs", is_insecure=ins,
                hop_port_range="20000-40000", subscription_port=8443, pin_sha256=pin)


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "cert").mkdir()
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
         "-keyout", str(root / "cert" / "server.key"), "-out", str(root / "cert" / "server.crt"),
         "-subj", "/CN=example.com", "-days", "365"],
        check=True, capture_output=True)
    pin = portal.get_cert_pin_sha256(root)
    fp = subprocess.run(["openssl", "x509", "-in", str(root / "cert" / "server.crt"), "-fingerprint", "-sha256", "-noout"],
                        capture_output=True, text=True).stdout
    expected = fp.split("=", 1)[1].replace(":", "").strip().lower()
    print("computed pin :", pin)
    print("openssl fp   :", expected)
    assert pin == expected, "pin does not match openssl -fingerprint"
    assert len(pin) == 64 and all(c in "0123456789abcdef" for c in pin)
    real_pin = pin

print("\n-- self-signed (real hex pin) --")
uri_ss, clash_ss, sing_ss = portal.artifacts(meta(True, real_pin))
print(uri_ss)
assert f"pinSHA256={real_pin}" in uri_ss, "hex pin missing from URI"
assert "insecure=1" in uri_ss
assert '"skip-cert-verify": true' in clash_ss
assert '"insecure": true' in sing_ss

print("\n-- publicly-trusted cert (pin must NOT be emitted) --")
uri_pub, clash_pub, sing_pub = portal.artifacts(meta(False, real_pin))
print(uri_pub)
assert "pinSHA256" not in uri_pub, "pin leaked on a publicly-trusted cert"
assert "insecure" not in uri_pub
assert '"skip-cert-verify": false' in clash_pub
assert '"insecure": false' in sing_pub

print("\n-- legacy/base64 pin in meta (must be rejected by the guard) --")
uri_b64, clash_b64, sing_b64 = portal.artifacts(meta(True, "xY7kQm2pLd9vRt4bNs1gWc6zHf3jKa8eUo5iTr2sQy0="))
print(uri_b64)
assert "pinSHA256" not in uri_b64, "base64 pin leaked back into the URI"
assert "insecure=1" in uri_b64, "insecure fallback lost"
assert "ca-sha256" not in clash_b64 and "certificate_pinned_sha256" not in sing_b64

print("\n-- sync_pin: client_meta.json cleanup (called by prepare/refresh) --")
import json
with tempfile.TemporaryDirectory() as td2:
    root2 = Path(td2)
    (root2 / "cert").mkdir()
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
         "-keyout", str(root2 / "cert" / "server.key"), "-out", str(root2 / "cert" / "server.crt"),
         "-subj", "/CN=example.com", "-days", "365"],
        check=True, capture_output=True)
    good = portal.get_cert_pin_sha256(root2)
    mp = root2 / "client_meta.json"

    mp.write_text(json.dumps(meta(False, "xY7kQm2pLd9vRt4bNs1gWc6zHf3jKa8eUo5iTr2sQy0=")))
    portal.sync_pin(json.loads(mp.read_text()), root2, mp)
    assert json.loads(mp.read_text())["pin_sha256"] == "", "stale pin survived on trusted cert"
    print("trusted  -> stale pin wiped        : OK")

    mp.write_text(json.dumps(meta(True, "xY7kQm2pLd9vRt4bNs1gWc6zHf3jKa8eUo5iTr2sQy0=")))
    portal.sync_pin(json.loads(mp.read_text()), root2, mp)
    assert json.loads(mp.read_text())["pin_sha256"] == good, "self-signed pin not recomputed"
    print("selfsign -> malformed pin recomputed: OK")

    mp.write_text(json.dumps(meta(True, "")))
    portal.sync_pin(json.loads(mp.read_text()), root2, mp)
    assert json.loads(mp.read_text())["pin_sha256"] == good, "missing self-signed pin not filled"
    print("selfsign -> missing pin filled      : OK")

print("\nALL ASSERTIONS PASSED")

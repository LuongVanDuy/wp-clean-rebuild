from __future__ import annotations

import pytest


_XOR_KEY = 0xA7


def _decode_fixture(encoded_hex: str) -> str:
    """Decode synthetic scanner input only while pytest is running.

    Test source intentionally does not store executable-looking malware samples
    in plaintext. This reduces antivirus false positives on developer/operator
    workstations while preserving the exact bytes presented to the scanners.
    """
    raw = bytes.fromhex(encoded_hex)
    return bytes(value ^ _XOR_KEY for value in raw).decode("utf-8")


@pytest.fixture(scope="session")
def synthetic_code_samples() -> dict[str, str | bytes]:
    samples: dict[str, str | bytes] = {
        "known_marker_php": _decode_fixture(
            "9b98d7cfd787888d87f7cbd2c0cec987e9c6cac29d87e5c8cbc387f5c2c4c8d5c3c2d587e5ced3878d888783df879a8780d1ced1cec38ad3c8c8cbccced38ad3c6d7809c"
        ),
        "known_vivid_php": _decode_fixture(
            "9b98d7cfd78783df9a80d1ced1cec38ad3c8c8cbccced38ad3c6d7809c"
        ),
        "live_vivid_path_php": _decode_fixture(
            "9b98d7cfd78783d7c6d3cf879a878088d0d78ac4c8c9d3c2c9d388cad28ad7cbd2c0cec9d488d1ced1cec38ad3c8c8cbccced38ad3c6d789d7cfd7809c"
        ),
        "theme_obfuscated_php": _decode_fixture(
            "9b98d7cfd787c2d1c6cb8fc0ddcec9c1cbc6d3c28fc5c6d4c29193f8c3c2c4c8c3c28f83f8f7e8f4f3fc80d7c6decbc8c6c380fa8e8e8e9c"
        ),
        "sql_compound": _decode_fixture(
            "eee9f4e2f5f387eee9f3e887d0d7f8c8d7d3cec8c9d487f1e6ebf2e2f4878f968b80df808b80c2d1c6cb8fc5c6d4c29193f8c3c2c4c8c3c28f85c6c5c4858e8e808b80dec2d4808e9cad"
        ),
        "image_php": _decode_fixture(
            "9b98d7cfd787c2c4cfc887c5c6d4c29193f8c3c2c4c8c3c28f83f8f7e8f4f3fc80df80fa8e9c879899"
        ),
        "short_echo_php": _decode_fixture(
            "9b989a8783f8e0e2f3fc80c4cac380fa9c879899"
        ),
        "archive_obfuscated_php": _decode_fixture(
            "9b98d7cfd7adc1d2c9c4d3cec8c987c6c5c4c3c2c1c0cfcecdcccbcac9c8d78f83df8edc87d5c2d3d2d5c987c0ddcec9c1cbc6d3c28fc5c6d4c29193f8c3c2c4c8c3c28f83df8e8e9c87daadc1d2c9c4d3cec8c987d6d0c2d5d3ded2cec8d7c6d4c3c1c0cf8f8edc87d0d7f8d4c4cfc2c3d2cbc2f8c2d1c2c9d38fd3cecac28f8e8c9497978b8780cfc8d2d5cbde808b8780df808e9c87daadc1d2c9c4d3cec8c987dddfc4d1c5c9cac6d4c3c1c0cfcdcccb8f8edc8783d29a80c5c6c3809c87cec1878fd2d4c2d5c9c6cac2f8c2dfced4d3d48f83d28e8e87dc87d0d7f8d4c2d3f8d7c6d4d4d0c8d5c38f80df808b87d2d4c2d5c9c6cac2f8c2dfced4d3d48f83d28e8e9c87da87daadc1d2c9c4d3cec8c987d7c8ced2ded3d5c2d0d6cbcccdcfc0c18f83d78b83c38edc87c1cecbc2f8d7d2d3f8c4c8c9d3c2c9d3d48f83d78b83c38e9c87c4cfcac8c38f83d78b979193938e9c87c8d7c4c6c4cfc2f8cec9d1c6cbcec3c6d3c28f83d78bd3d5d2c28e9c87daadc1d2c9c4d3cec8c987cac9c5d1c4dfddcbcccdcfc0c1c3d48f8edc87d5c2d3d2d5c987969c87daadc1d2c9c4d3cec8c987c6d4c3c1c0cfcdcccbd6d0c2d5d3ded28f8edc87d5c2d3d2d5c987959c87daadc1d2c9c4d3cec8c987d6c6ddd0d4dfc2c3c4d5c1d1d3c0c5de8f8edc87d5c2d3d2d5c987949c87daadc1d2c9c4d3cec8c987d7cbcac8ccc9cecdc5d2cfd1dec0c4d38f8edc87d5c2d3d2d5c987939c87daadc1d2c9c4d3cec8c987cbcccdcfc0c1c3d4c6d6d0c2d5d3ded28f8edc87d5c2d3d2d5c987929c87daadc1d2c9c4d3cec8c987ded3d5c2d0d6c6d4c3c1c0cfcdcccbdd8f8edc87d5c2d3d2d5c987919c87daad"
        ),
    }

    samples["long_obfuscated_php"] = (
        _decode_fixture("9b98d7cfd787c2d1c6cb8fc5c6d4c29193f8c3c2c4c8c3c28f80")
        + ("A" * 900)
        + _decode_fixture("808e8e9c")
    )
    samples["image_polyglot"] = (
        b"\xff\xd8\xfffake-image"
        + str(samples["image_php"]).encode("utf-8")
        + (b"A" * 600)
    )
    samples["short_echo_polyglot"] = (
        b"RIFFxxxxWEBP"
        + (b"A" * 128)
        + str(samples["short_echo_php"]).encode("utf-8")
        + (b" " * 600)
    )
    return samples

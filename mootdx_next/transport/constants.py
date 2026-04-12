RSP_HEADER_LEN = 0x10
DEFAULT_CONNECT_TIMEOUT_MS = 5000
DEFAULT_HEARTBEAT_INTERVAL_SEC = 10.0
STD_SETUP_PAYLOADS = (
    bytes.fromhex("0c 02 18 93 00 01 03 00 03 00 0d 00 01"),
    bytes.fromhex("0c 02 18 94 00 01 03 00 03 00 0d 00 02"),
    bytes.fromhex(
        "0c 03 18 99 00 01 20 00 20 00 db 0f d5 d0"
        "c9 cc d6 a4 a8 af 00 00 00 8f c2 25 40 13"
        "00 00 d5 00 c9 cc bd f0 d7 ea 00 00 00 02"
    ),
)

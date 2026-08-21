RSP_HEADER_LEN = 0x10
DEFAULT_CONNECT_TIMEOUT_MS = 5000
DEFAULT_HEARTBEAT_INTERVAL_SEC = 10.0
STD_SETUP_PAYLOADS = (
    bytes.fromhex("0c 02 18 93 00 01 03 00 03 00 0d 00 01"),
    bytes.fromhex("0c 02 18 94 00 01 03 00 03 00 0d 00 02"),
    # Do not send the legacy 42-byte client identity payload below. In a live
    # audit, 25 of 26 reachable HQ servers downgraded sessions advertising it:
    # quotes returned a 4-byte empty status and bars a 2-byte empty status.
    # Keeping the payload commented out documents the old protocol only.
    # bytes.fromhex(
    #     "0c 03 18 99 00 01 20 00 20 00 db 0f d5 d0"
    #     "c9 cc d6 a4 a8 af 00 00 00 8f c2 25 40 13"
    #     "00 00 d5 00 c9 cc bd f0 d7 ea 00 00 00 02"
    # ),
)

EX_SETUP_PAYLOADS = (
    bytes.fromhex(
        "01 01 48 65 00 01 52 00 52 00 54 24 1f 32 c6 e5"
        "d5 3d fb 41 1f 32 c6 e5 d5 3d fb 41 1f 32 c6 e5"
        "d5 3d fb 41 1f 32 c6 e5 d5 3d fb 41 1f 32 c6 e5"
        "d5 3d fb 41 1f 32 c6 e5 d5 3d fb 41 1f 32 c6 e5"
        "d5 3d fb 41 1f 32 c6 e5 d5 3d fb 41 cc e1 6d ff"
        "d5 ba 3f b8 cb c5 7a 05 4f 77 48 ea"
    ),
)

EX_INSTRUMENT_COUNT_PAYLOAD = bytes.fromhex("01 03 48 66 00 01 02 00 02 00 f0 23")

MAC_EX_LOGIN_PAYLOAD = bytes.fromhex(
    "01 00 00 00 00 01 52 00 52 00"
    "54 24"
    "e5bb1c2fafe52594 1f32c6e5d53dfb41 5b734cc9cdbf0ac9"
    "2021bfdd1eb06d22 d008884c1611cb13 78f6abd824d899d2"
    "1f32c6e5d53dfb41 1f32c6e5d53dfb41 a9325ac935dc0837"
    "335a16e4ce17c1bb"
)
